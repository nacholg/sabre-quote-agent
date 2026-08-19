from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.models.api import AgentQuoteRequest, AgentQuoteResponse, FareRuleAuditResponse, QuoteRenderResponse, QuoteSearchAPIRequest, QuoteSearchAPIResponse, QuoteSelectionRequest, QuoteSelectionResponse, StoredQuoteRecord, StoredQuoteSummary, QuoteWorkflowUpdate, QuoteWorkflowResponse, QuoteRefreshResponse
from app.models.commercial_quote import CommercialQuote
from app.sabre.errors import SabreError
from app.services.quote_service import search_quote
from app.services.agent_service import agent_quote
from app.services.quote_repository import get_quote_repository
from app.services.commercial_renderer import render_stored_quote
from app.services.commercial_quote_builder import build_commercial_quote
from app.services.live_air_rules_audit import audit_stored_quote_live
from app.services.fare_rule_response import prepare_fare_rule_response
from app.services.reference_repository import get_reference_repository
from app.services.quote_refresh import refresh_stored_quote

app = FastAPI(
    title="Sabre Quote Agent",
    version="0.18.2",
    description="API read-only para buscar, normalizar y presentar cotizaciones Sabre BFM.",
)


WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
async def web_app() -> HTMLResponse:
    return HTMLResponse(WEB_INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "sabre-quote-agent", "version": "0.18.2"}


@app.post("/quotes/search", response_model=QuoteSearchAPIResponse)
async def quotes_search(request: QuoteSearchAPIRequest) -> QuoteSearchAPIResponse:
    try:
        return await search_quote(request)
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.post("/agent/quote", response_model=AgentQuoteResponse)
async def agent_quote_endpoint(request: AgentQuoteRequest) -> AgentQuoteResponse:
    try:
        return await agent_quote(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.get("/quotes", response_model=list[StoredQuoteSummary])
async def list_quotes(limit: int = 20) -> list[StoredQuoteSummary]:
    limit = min(100, max(1, limit))
    return get_quote_repository().list(limit=limit)


@app.get("/quotes/{quote_id}", response_model=StoredQuoteRecord)
async def get_quote(quote_id: str) -> StoredQuoteRecord:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    return record



@app.get(
    "/quotes/{quote_id}/commercial",
    response_model=CommercialQuote,
    summary="Cotización comercial canónica",
)
async def get_commercial_quote(
    quote_id: str,
    selected_only: bool = False,
) -> CommercialQuote:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )

    try:
        return build_commercial_quote(
            record,
            selected_only=selected_only,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.post("/quotes/{quote_id}/select", response_model=QuoteSelectionResponse)
async def select_quote_options(
    quote_id: str,
    request: QuoteSelectionRequest,
) -> QuoteSelectionResponse:
    try:
        return get_quote_repository().select(quote_id, request.ranks)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/quotes/{quote_id}/select", response_model=QuoteSelectionResponse)
async def clear_quote_selection(quote_id: str) -> QuoteSelectionResponse:
    try:
        return get_quote_repository().clear_selection(quote_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")


@app.get("/quotes/{quote_id}/render", response_model=QuoteRenderResponse)
async def render_quote(
    quote_id: str,
    format: str = Query(default="whatsapp", pattern="^(whatsapp|email)$"),
) -> QuoteRenderResponse:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    try:
        return render_stored_quote(record, format)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc



@app.get(
    "/quotes/{quote_id}/whatsapp",
    response_class=PlainTextResponse,
    summary="Render WhatsApp directo",
)
async def render_quote_whatsapp(quote_id: str) -> PlainTextResponse:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    try:
        rendered = render_stored_quote(record, "whatsapp")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PlainTextResponse(
        content=rendered.content,
        media_type="text/plain; charset=utf-8",
    )


@app.get(
    "/quotes/{quote_id}/email",
    response_class=HTMLResponse,
    summary="Render Email HTML directo",
)
async def render_quote_email(quote_id: str) -> HTMLResponse:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    try:
        rendered = render_stored_quote(record, "email")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return HTMLResponse(
        content=rendered.content,
        media_type="text/html; charset=utf-8",
    )



@app.get(
    "/quotes/{quote_id}/fare-rules",
    response_model=FareRuleAuditResponse,
    summary="Auditar confiabilidad de condiciones tarifarias",
)
async def quote_fare_rules(
    quote_id: str,
    selected_only: bool = True,
    include_source_text: bool = False,
) -> FareRuleAuditResponse:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    response = audit_stored_quote_live(record, selected_only=selected_only)
    return prepare_fare_rule_response(
        response,
        include_source_text=include_source_text,
    )


@app.get("/reference/stats", summary="Estadísticas del catálogo local")
async def reference_stats() -> dict[str, int]:
    return get_reference_repository().stats()


@app.get("/reference/resolve", summary="Resolver alias en el catálogo local")
async def reference_resolve(
    q: str,
    type: str = Query(pattern="^(airport|city|airline)$"),
) -> dict[str, object]:
    codes = get_reference_repository().resolve_exact(q, type)
    return {"query": q, "type": type, "codes": codes}


@app.patch("/quotes/{quote_id}/workflow", response_model=QuoteWorkflowResponse)
async def update_quote_workflow(
    quote_id: str,
    request: QuoteWorkflowUpdate,
) -> QuoteWorkflowResponse:
    try:
        return get_quote_repository().update_workflow(
            quote_id,
            client_name=request.client_name,
            client_reference=request.client_reference,
            notes=request.notes,
            status=request.status,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/quotes/{quote_id}/refresh", response_model=QuoteRefreshResponse)
async def refresh_quote(quote_id: str) -> QuoteRefreshResponse:
    try:
        return await refresh_stored_quote(get_quote_repository(), quote_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
