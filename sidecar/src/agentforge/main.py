"""FastAPI application entry point for the AgentForge sidecar.

MVP wiring:
  * /health  — liveness probe
  * /turn    — one agent turn: validate JWT, run orchestrator, return reply

The auth gateway, LLM client, demographics fetcher, and orchestrator are
constructed in `create_app()` and stashed on `app.state` so the route
handlers can pull them via FastAPI's dependency system. Production
hardening (Redis-backed sessions, Langfuse tracing, the verifier) is
deferred to later tasks. See ARCHITECTURE.md §1.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from agentforge.config import Settings, get_settings
from agentforge.gateway.auth_gateway import (
    AuthGateway,
    RequestContext,
    get_request_context,
)
from agentforge.llm.claude import ClaudeClient
from agentforge.llm.client import LLMClient
from agentforge.orchestrator import Orchestrator
from agentforge.tools.demographics import DemographicsFetcher


class TurnRequest(BaseModel):
    message: str


class TurnResponse(BaseModel):
    reply: str


def get_orchestrator(request: Request) -> Orchestrator:
    orchestrator = request.app.state.orchestrator
    if not isinstance(orchestrator, Orchestrator):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Orchestrator is not configured on app.state",
        )
    return orchestrator


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    demographics_fetcher: DemographicsFetcher | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Dependencies are injectable so tests can swap in fakes without
    touching the network or environment. `uvicorn agentforge.main:create_app
    --factory` is the production entry point.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
    )

    auth_gateway = AuthGateway(jwt_secret=settings.jwt_secret)
    llm = llm_client or ClaudeClient(api_key=settings.anthropic_api_key)
    fetcher = demographics_fetcher or DemographicsFetcher(
        base_url=settings.openemr_base_url,
    )
    orchestrator = Orchestrator(llm=llm, demographics_fetcher=fetcher)

    app.state.auth_gateway = auth_gateway
    app.state.orchestrator = orchestrator

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.post("/turn", response_model=TurnResponse)
    async def turn(
        body: TurnRequest,
        ctx: Annotated[RequestContext, Depends(get_request_context)],
        orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
    ) -> TurnResponse:
        reply = await orchestrator.turn(ctx, body.message)
        return TurnResponse(reply=reply)

    return app
