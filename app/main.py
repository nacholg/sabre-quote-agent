from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import SabreEnvironmentMismatchError, runtime_environment_status
from app.models.api import AgentQuoteRequest, AgentQuoteResponse, FareRuleAuditResponse, QuoteRenderResponse, QuoteSearchAPIRequest, QuoteSearchAPIResponse, QuoteSelectionRequest, QuoteSelectionResponse, StoredQuoteRecord, StoredQuoteSummary, QuoteWorkflowUpdate, QuoteWorkflowResponse, QuoteRefreshResponse
from app.models.commercial_quote import CommercialQuote
from app.models.api import QuoteArtifactCreate, QuoteArtifactRecord, QuoteVersionHistory
from app.models.api import QuoteModificationRequest, QuoteModificationResponse
from app.sabre.errors import SabreError
from app.services.quote_service import search_quote
from app.services.agent_service import agent_quote
from app.services.quote_repository import QuoteRepositoryUnavailableError, QuoteVersionConflictError, get_quote_repository
from app.services.commercial_renderer import render_stored_quote
from app.services.commercial_quote_builder import build_commercial_quote
from app.services.live_air_rules_audit import audit_stored_quote_live
from app.services.fare_rule_response import prepare_fare_rule_response
from app.services.reference_repository import get_reference_repository
from app.services.quote_refresh import refresh_stored_quote
from app.services.quote_modification import modify_stored_quote

app = FastAPI(
    title="Sabre Quote Agent",
    version="0.21.1",
    description="API read-only para buscar, normalizar y presentar cotizaciones Sabre BFM.",
)


WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


_DATABASE_UNAVAILABLE_DETAIL = (
    "Base de datos no disponible. Verificá DATABASE_URL y la conectividad "
    "con PostgreSQL antes de continuar."
)


@app.exception_handler(QuoteRepositoryUnavailableError)
async def quote_repository_unavailable_handler(_, exc):
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc) or _DATABASE_UNAVAILABLE_DETAIL},
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_unavailable_handler(_, __):
    # Do not leak hostnames, ports, users, passwords or SQLAlchemy internals.
    return JSONResponse(
        status_code=503,
        content={"detail": _DATABASE_UNAVAILABLE_DETAIL},
    )


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
async def web_app() -> HTMLResponse:
    return HTMLResponse(WEB_INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, object]:
    database: dict[str, object] = {
        "status": "unavailable",
        "dialect": None,
    }

    try:
        repository = get_quote_repository()
        repository.ping()
        database = {
            "status": "ok",
            "dialect": repository.dialect_name,
        }
    except (QuoteRepositoryUnavailableError, SQLAlchemyError):
        pass

    return {
        "status": "ok" if database["status"] == "ok" else "degraded",
        "service": "sabre-quote-agent",
        "version": "0.21.1",
        "database": database,
    }


@app.get("/runtime", summary="Runtime operativo sin secretos")
async def runtime_status() -> dict[str, object]:
    return runtime_environment_status()


@app.post("/quotes/search", response_model=QuoteSearchAPIResponse)
async def quotes_search(request: QuoteSearchAPIRequest) -> QuoteSearchAPIResponse:
    try:
        return await search_quote(request)
    except SabreEnvironmentMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.post("/agent/quote", response_model=AgentQuoteResponse)
async def agent_quote_endpoint(request: AgentQuoteRequest) -> AgentQuoteResponse:
    try:
        return await agent_quote(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SabreEnvironmentMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc



@app.get("/quotes", response_model=list[StoredQuoteSummary])
async def list_quotes(
    limit: int = Query(default=50, ge=1, le=100),
    q: str | None = Query(default=None, max_length=160),
) -> list[StoredQuoteSummary]:
    return get_quote_repository().list(
        limit=limit,
        search=q,
    )


@app.get("/quotes/{quote_id}", response_model=StoredQuoteRecord)
async def get_quote(quote_id: str) -> StoredQuoteRecord:
    record = get_quote_repository().get(quote_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")

    # Expansion candidates remain server-side and are fetched progressively
    # through the commercial endpoint.
    public_record = record.model_copy(deep=True)
    public_record.quote_response.pop("_candidate_options", None)
    return public_record



@app.get(
    "/quotes/{quote_id}/versions",
    response_model=QuoteVersionHistory,
    summary="Historial de versiones de una cotización",
)
async def get_quote_versions(
    quote_id: str,
) -> QuoteVersionHistory:
    try:
        return get_quote_repository().version_history(quote_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.get(
    "/quotes/{quote_id}/commercial",
    response_model=CommercialQuote,
    summary="Cotización comercial canónica",
)
async def get_commercial_quote(
    quote_id: str,
    selected_only: bool = False,
    offset: int = Query(default=0, ge=0, le=49),
    limit: int | None = Query(default=None, ge=1, le=50),
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
            offset=offset,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc


@app.get(
    "/quotes/{quote_id}/artifacts",
    response_model=list[QuoteArtifactRecord],
    summary="Salidas persistidas de una cotización",
)
async def list_quote_artifacts(
    quote_id: str,
) -> list[QuoteArtifactRecord]:
    repository = get_quote_repository()
    if repository.get(quote_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )
    return [
        QuoteArtifactRecord.model_validate(item)
        for item in repository.list_artifacts(quote_id)
    ]


@app.post(
    "/quotes/{quote_id}/artifacts",
    response_model=QuoteArtifactRecord,
    summary="Guardar una salida generada",
)
async def create_quote_artifact(
    quote_id: str,
    payload: QuoteArtifactCreate,
) -> QuoteArtifactRecord:
    repository = get_quote_repository()
    if repository.get(quote_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )

    item = repository.create_artifact(
        quote_id,
        artifact_type=payload.artifact_type,
        title=payload.title,
        selected_ranks=payload.selected_ranks,
        content_type=payload.content_type,
        content=payload.content,
    )
    return QuoteArtifactRecord.model_validate(item)


@app.delete(
    "/quotes/{quote_id}/artifacts/{artifact_id}",
    summary="Eliminar una salida generada",
)
async def delete_quote_artifact(
    quote_id: str,
    artifact_id: int,
) -> dict:
    repository = get_quote_repository()
    if repository.get(quote_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )

    deleted = repository.delete_artifact(quote_id, artifact_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Salida no encontrada: {artifact_id}",
        )

    return {
        "quote_id": quote_id,
        "artifact_id": artifact_id,
        "deleted": True,
    }


@app.delete(
    "/quotes/{quote_id}/artifacts",
    summary="Eliminar todas las salidas de una cotización",
)
async def clear_quote_artifacts(
    quote_id: str,
) -> dict:
    repository = get_quote_repository()
    if repository.get(quote_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )

    deleted_count = repository.clear_artifacts(quote_id)
    return {
        "quote_id": quote_id,
        "deleted_count": deleted_count,
    }


@app.post("/quotes/{quote_id}/select", response_model=QuoteSelectionResponse)
async def select_quote_options(
    quote_id: str,
    request: QuoteSelectionRequest,
) -> QuoteSelectionResponse:
    try:
        return get_quote_repository().select(
            quote_id,
            request.ranks,
            request.fares,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except QuoteVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/quotes/{quote_id}/select", response_model=QuoteSelectionResponse)
async def clear_quote_selection(quote_id: str) -> QuoteSelectionResponse:
    try:
        return get_quote_repository().clear_selection(quote_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except QuoteVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    except QuoteVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/quotes/{quote_id}/modify",
    response_model=QuoteModificationResponse,
    summary="Modificar conversacionalmente una cotización",
)
async def modify_quote(
    quote_id: str,
    request: QuoteModificationRequest,
) -> QuoteModificationResponse:
    try:
        return await modify_stored_quote(
            get_quote_repository(),
            quote_id,
            request,
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Cotización no encontrada: {quote_id}",
        )
    except QuoteVersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except SabreEnvironmentMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    except SabreError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/quotes/{quote_id}/refresh", response_model=QuoteRefreshResponse)
async def refresh_quote(quote_id: str) -> QuoteRefreshResponse:
    try:
        return await refresh_stored_quote(get_quote_repository(), quote_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Cotización no encontrada: {quote_id}")
    except QuoteVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SabreEnvironmentMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SabreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
