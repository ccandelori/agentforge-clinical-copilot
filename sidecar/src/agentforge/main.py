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

import asyncio
import builtins
import logging
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from agentforge.config import Settings, get_settings
from agentforge.gateway.auth_gateway import (
    AuthGateway,
    RequestContext,
    get_request_context,
)
from agentforge.gateway.policy_loader import POLICY_LOADED_KEY, load_sensitivity_policy
from agentforge.llm.claude import ClaudeClient
from agentforge.llm.client import LLMClient
from agentforge.orchestrator import Orchestrator
from agentforge.tools.allergies import AllergiesFetcher
from agentforge.tools.demographics import DemographicsFetcher
from agentforge.tools.labs import LabsFetcher
from agentforge.tools.medications import MedicationsFetcher
from agentforge.tools.problems import ProblemsFetcher
from agentforge.tools.vitals import VitalsFetcher

logger = logging.getLogger(__name__)


class _AppRedisProto(Protocol):
    """Combined Redis surface used by the policy loader and the
    record-visibility check. Typed as Protocol so tests can pass an
    `AsyncMock` straight through `create_app(redis_client=...)`."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes | str) -> object: ...

    async def delete(self, *keys: str) -> object: ...

    async def keys(self, pattern: str) -> list[bytes] | list[str]: ...

    async def smembers(self, key: str) -> builtins.set[bytes]: ...


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
    medications_fetcher: MedicationsFetcher | None = None,
    problems_fetcher: ProblemsFetcher | None = None,
    allergies_fetcher: AllergiesFetcher | None = None,
    labs_fetcher: LabsFetcher | None = None,
    vitals_fetcher: VitalsFetcher | None = None,
    redis_client: _AppRedisProto | None = None,
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

    auth_gateway = AuthGateway(jwt_secret=settings.jwt_secret, redis_client=redis_client)
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

    app.state.auth_gateway = auth_gateway
    app.state.orchestrator = orchestrator
    app.state.redis_client = redis_client

    # Sensitivity-policy load runs synchronously at startup so a bad
    # policy fails the boot before any request is served. ARCHITECTURE.md
    # §2 requires this — fail-closed on policy unavailability.
    if redis_client is not None:
        try:
            asyncio.run(
                load_sensitivity_policy(redis_client, settings.sensitivity_policy_path),
            )
        except Exception:
            if settings.sensitivity_policy_required:
                raise
            logger.warning(
                "Sensitivity policy load failed; continuing because "
                "SENSITIVITY_POLICY_REQUIRED=false",
                exc_info=True,
            )

    @app.get("/health")
    async def health() -> dict[str, object]:
        policy_loaded = False
        if redis_client is not None:
            sentinel = await redis_client.get(POLICY_LOADED_KEY)
            policy_loaded = sentinel is not None
        return {"status": "healthy", "policy_loaded": policy_loaded}

    @app.post("/turn", response_model=TurnResponse)
    async def turn(
        body: TurnRequest,
        ctx: Annotated[RequestContext, Depends(get_request_context)],
        orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
    ) -> TurnResponse:
        reply = await orchestrator.turn(ctx, body.message)
        return TurnResponse(reply=reply)

    return app
