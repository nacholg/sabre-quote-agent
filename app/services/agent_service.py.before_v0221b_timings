from __future__ import annotations

from app.models.api import AgentQuoteRequest, AgentQuoteResponse
from app.services.agent_parser import parse_agent_quote
from app.services.quote_service import search_quote
from app.services.quote_repository import get_quote_repository


async def agent_quote(request: AgentQuoteRequest) -> AgentQuoteResponse:
    interpretation = parse_agent_quote(request)
    quote = None
    if request.execute and not interpretation.search_request.has_mixed_leg_cabins:
        quote = await search_quote(interpretation.search_request)
        if quote.quote_id:
            get_quote_repository().attach_agent_context(
                quote.quote_id,
                text=request.text,
                interpretation=interpretation,
            )
    return AgentQuoteResponse(interpretation=interpretation, quote=quote)
