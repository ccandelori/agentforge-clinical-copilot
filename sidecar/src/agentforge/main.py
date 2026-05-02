"""FastAPI application entry point for the AgentForge sidecar.

MVP wiring:
  * /health  — liveness probe
  * /turn    — one agent turn: validate JWT, run orchestrator, return reply

The auth gateway, LLM client, fetchers, orchestrator, and Redis storage
client are constructed in `create_app()` and stashed on `app.state` so
the route handlers can pull them via FastAPI's dependency system. The
Redis client is allocated here but not yet wired into the orchestrator;
session-memory integration ships as a follow-up task. Langfuse tracing
and the verifier are still deferred. See ARCHITECTURE.md §1.
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
from agentforge.observability import (
    AgentLangfuse,
    LangfuseClient,
    NullLangfuseClient,
)
from agentforge.orchestrator import Orchestrator
from agentforge.orchestrator.memory import ConversationMemory
from agentforge.storage.redis_client import AgentRedisClient
from agentforge.tools.allergies import AllergiesFetcher
from agentforge.tools.demographics import DemographicsFetcher
from agentforge.tools.labs import LabsFetcher
from agentforge.tools.medications import MedicationsFetcher
from agentforge.tools.notes import NotesFetcher
from agentforge.tools.problems import ProblemsFetcher
from agentforge.tools.vitals import VitalsFetcher
from agentforge.verifier import DomainConstraints

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
    # Multi-turn memory key (Task 31). The frontend mints this once at
    # conversation start (see :func:`generate_session_id`) and sends it
    # back on every subsequent turn to opt into persisted history.
    # ``None`` disables memory for this turn — the agent is single-shot.
    session_id: str | None = None


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
    notes_fetcher: NotesFetcher | None = None,
    redis_storage: AgentRedisClient | None = None,
    langfuse_client: LangfuseClient | None = None,
    redis_client: _AppRedisProto | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Dependencies are injectable so tests can swap in fakes without
    touching the network or environment. `uvicorn agentforge.main:create_app
    --factory` is the production entry point.
    """
    settings = settings or get_settings()

    storage = redis_storage or AgentRedisClient(redis_url=settings.redis_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Sensitivity policy loads at startup so a bad policy fails the
        # boot before any request is served — fail-closed per
        # ARCHITECTURE.md §2. Load lives in the lifespan (not the sync
        # factory) so we can await Redis cleanly under uvicorn.
        if redis_client is not None:
            try:
                await load_sensitivity_policy(
                    redis_client, settings.sensitivity_policy_path
                )
            except Exception:
                if settings.sensitivity_policy_required:
                    raise
                logger.warning(
                    "Sensitivity policy load failed; continuing because "
                    "SENSITIVITY_POLICY_REQUIRED=false",
                    exc_info=True,
                )
        try:
            yield
        finally:
            # Flush pending Langfuse traces and tear down the OTel exporter
            # thread so a graceful shutdown doesn't leak in-flight spans;
            # then release the Redis connection pool.
            await _app.state.langfuse.aclose()
            await storage.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
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
    notes = notes_fetcher or NotesFetcher(
        base_url=settings.openemr_base_url,
        gateway=auth_gateway,
    )
    langfuse: LangfuseClient = langfuse_client or _build_langfuse(settings)

    orchestrator = Orchestrator(
        llm=llm,
        demographics_fetcher=demographics,
        medications_fetcher=medications,
        problems_fetcher=problems,
        allergies_fetcher=allergies,
        labs_fetcher=labs,
        vitals_fetcher=vitals,
        notes_fetcher=notes,
        domain_constraints=DomainConstraints(),
        verifier_enabled=settings.verifier_enabled,
        langfuse=langfuse,
        hmac_key=settings.hmac_key.encode("utf-8"),
        redis_storage=storage,
        memory=ConversationMemory(redis_storage=storage),
    )

    app.state.auth_gateway = auth_gateway
    app.state.orchestrator = orchestrator
    app.state.redis_storage = storage
    app.state.langfuse = langfuse
    app.state.redis_client = redis_client

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
        reply = await orchestrator.turn(
            ctx, body.message, session_id=body.session_id
        )
        return TurnResponse(reply=reply)

    return app
