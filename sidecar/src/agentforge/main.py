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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from agentforge.observability import (
    AgentLangfuse,
    LangfuseClient,
    NullLangfuseClient,
)
from agentforge.orchestrator import Orchestrator
from agentforge.tools.allergies import AllergiesFetcher
from agentforge.tools.demographics import DemographicsFetcher
from agentforge.tools.labs import LabsFetcher
from agentforge.tools.medications import MedicationsFetcher
from agentforge.tools.problems import ProblemsFetcher
from agentforge.tools.vitals import VitalsFetcher


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


def _build_langfuse(settings: Settings) -> LangfuseClient:
    """Pick the Langfuse implementation based on configuration.

    Returns :class:`AgentLangfuse` only when host + both keys are set.
    Anything missing falls back to :class:`NullLangfuseClient` so local
    dev and tests never need a running Langfuse instance.
    """
    hmac_key_bytes = settings.hmac_key.encode("utf-8")
    if (
        settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    ):
        return AgentLangfuse(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            hmac_key=hmac_key_bytes,
        )
    return NullLangfuseClient(hmac_key=hmac_key_bytes)


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    demographics_fetcher: DemographicsFetcher | None = None,
    medications_fetcher: MedicationsFetcher | None = None,
    problems_fetcher: ProblemsFetcher | None = None,
    allergies_fetcher: AllergiesFetcher | None = None,
    labs_fetcher: LabsFetcher | None = None,
    vitals_fetcher: VitalsFetcher | None = None,
    langfuse_client: LangfuseClient | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Dependencies are injectable so tests can swap in fakes without
    touching the network or environment. `uvicorn agentforge.main:create_app
    --factory` is the production entry point.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Flush pending traces and tear down the OTel exporter thread
            # so a graceful shutdown doesn't leak in-flight spans.
            await _app.state.langfuse.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    auth_gateway = AuthGateway(jwt_secret=settings.jwt_secret)
    llm = llm_client or ClaudeClient(api_key=settings.anthropic_api_key)
    demographics = demographics_fetcher or DemographicsFetcher(
        base_url=settings.openemr_base_url,
    )
    medications = medications_fetcher or MedicationsFetcher(
        base_url=settings.openemr_base_url,
    )
    problems = problems_fetcher or ProblemsFetcher(
        base_url=settings.openemr_base_url,
    )
    allergies = allergies_fetcher or AllergiesFetcher(
        base_url=settings.openemr_base_url,
    )
    labs = labs_fetcher or LabsFetcher(
        base_url=settings.openemr_base_url,
    )
    vitals = vitals_fetcher or VitalsFetcher(
        base_url=settings.openemr_base_url,
    )
    orchestrator = Orchestrator(
        llm=llm,
        demographics_fetcher=demographics,
        medications_fetcher=medications,
        problems_fetcher=problems,
        allergies_fetcher=allergies,
        labs_fetcher=labs,
        vitals_fetcher=vitals,
    )

    langfuse: LangfuseClient = langfuse_client or _build_langfuse(settings)

    app.state.auth_gateway = auth_gateway
    app.state.orchestrator = orchestrator
    app.state.langfuse = langfuse

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
