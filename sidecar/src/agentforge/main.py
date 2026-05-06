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
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast

import redis.asyncio as redis_async
from anthropic import AsyncAnthropic
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentforge.breakglass import BreakglassAuditTool
from agentforge.config import Settings, get_settings
from agentforge.gateway.auth_gateway import (
    AuthGateway,
    RequestContext,
    get_request_context,
)
from agentforge.gateway.policy_loader import POLICY_LOADED_KEY, load_sensitivity_policy
from agentforge.llm.claude import ClaudeClient
from agentforge.llm.client import LLMClient
from agentforge.llm.types import StreamFinal, StreamTextDelta
from agentforge.observability import (
    AgentLangfuse,
    LangfuseClient,
    NullLangfuseClient,
)
from agentforge.orchestrator import (
    Orchestrator,
    _AgentGraphLike,
    get_last_trace_id,
    get_last_turn_extraction,
    get_turn_cost_usd,
)
from agentforge.orchestrator.graph import build_graph
from agentforge.orchestrator.memory import ConversationMemory
from agentforge.orchestrator.planner import Planner
from agentforge.orchestrator.truncation import SynthesisInputTruncator
from agentforge.rag import (
    BM25Retriever,
    CrossEncoderReranker,
    DenseRetriever,
    EvidenceRetriever,
    RRFMerger,
    SentenceTransformerCrossEncoder,
    SentenceTransformerEncoder,
    load_corpus,
)
from agentforge.storage.redis_client import AgentRedisClient
from agentforge.tools.allergies import AllergiesFetcher
from agentforge.tools.attach_and_extract import (
    INTAKE_CONTRACT,
    PdfRenderer,
    RenderedPage,
    VisionExtractor,
)
from agentforge.tools.demographics import DemographicsFetcher
from agentforge.tools.document_bytes import DocumentBytesFetcher, DocumentBytesFetchError
from agentforge.tools.encounters import EncountersFetcher
from agentforge.tools.immunizations import ImmunizationsFetcher
from agentforge.tools.labs import LabsFetcher
from agentforge.tools.medications import MedicationsFetcher
from agentforge.tools.notes import NotesFetcher
from agentforge.tools.problems import ProblemsFetcher
from agentforge.tools.procedures import ProceduresFetcher
from agentforge.tools.search_notes import SearchNotesFetcher
from agentforge.tools.vitals import VitalsFetcher
from agentforge.verifier.data_quality import DataQualityChecker

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
    # W2 inputs (MR 7). When ``document_id`` is set the /turn handler
    # fetches the document bytes via the JWT-validated PHP endpoint,
    # renders them to per-page PNGs, and routes the turn through the
    # graph's intake-extractor node. ``evidence_query`` is forwarded
    # to the orchestrator verbatim — the graph routes to the evidence
    # node when it is non-empty. Either field, both, or neither may be
    # set; the orchestrator falls back to the W1 iterative loop when
    # neither is present so chart-question turns are unaffected.
    document_id: int | None = None
    evidence_query: str = ""


class TurnResponse(BaseModel):
    reply: str
    # Structured extraction snapshot from the W2 INTAKE flow (MR 7
    # follow-up). Populated when the request supplied a
    # ``document_id`` and the graph's intake-extractor node ran;
    # ``None`` for chart-question turns, evidence-only turns, and
    # turns that fell through to the W1 iterative loop. The browser
    # renders this beneath the synthesized chat bubble so a
    # clinician can confirm what was actually parsed from the PDF
    # before relying on it (the chat reply is a summary; this dict
    # is the receipts).
    extraction: dict[str, Any] | None = None


def get_orchestrator(request: Request) -> Orchestrator:
    orchestrator = request.app.state.orchestrator
    if not isinstance(orchestrator, Orchestrator):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Orchestrator is not configured on app.state",
        )
    return orchestrator


def get_document_bytes_fetcher(request: Request) -> DocumentBytesFetcher:
    """Pull the request-scoped DocumentBytesFetcher off app.state.

    Exposed as a FastAPI dependency so the W2 /turn path injects it
    via ``Depends`` and tests can override it via
    ``app.dependency_overrides``. The instance itself is process-wide
    (one ``httpx.AsyncClient`` shared across requests).
    """
    fetcher = request.app.state.document_bytes_fetcher
    if not isinstance(fetcher, DocumentBytesFetcher):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DocumentBytesFetcher is not configured on app.state",
        )
    return fetcher


def get_pdf_renderer(request: Request) -> PdfRenderer:
    """Pull the request-scoped PdfRenderer off app.state.

    Same pattern as :func:`get_document_bytes_fetcher` — process-wide
    instance, request-scoped dependency injection.
    """
    renderer = request.app.state.pdf_renderer
    if not isinstance(renderer, PdfRenderer):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PdfRenderer is not configured on app.state",
        )
    return renderer


async def _sse_stream(
    orchestrator: Orchestrator,
    ctx: RequestContext,
    body: TurnRequest,
) -> AsyncIterator[str]:
    """Format ``orchestrator.stream_turn`` events as a Server-Sent Events
    stream (week1-gaps Task #10).

    Wire shape, per :rfc:`8895` SSE conventions:

    .. code-block:: text

        data: {"text": "Hello, "}

        data: {"text": "Susan!"}

        data: {"final": true, "stop_reason": "end_turn", "cost_usd": 0.001234}

        data: [DONE]

    Each frame is one ``data:`` line followed by an empty line. The
    terminal ``[DONE]`` sentinel matches the OpenAI / Anthropic
    streaming convention so JS readers built against either provider
    can consume our stream without reshaping.

    Cost is emitted on the ``final`` frame because we can't set an
    HTTP header after the response body has started; the PHP proxy
    parses the final frame and re-emits the cost as the same
    ``X-Agent-Cost-USD`` header it surfaces today.
    """
    async for event in orchestrator.stream_turn(
        ctx, body.message, session_id=body.session_id
    ):
        if isinstance(event, StreamTextDelta):
            yield f"data: {json.dumps({'text': event.text})}\n\n"
        elif isinstance(event, StreamFinal):
            payload = {
                "final": True,
                "stop_reason": event.response.stop_reason,
                "cost_usd": round(get_turn_cost_usd(), 6),
            }
            yield f"data: {json.dumps(payload)}\n\n"

    # Terminator. SSE consumers know the stream is done when the
    # connection closes; ``[DONE]`` is the upstream-compatible explicit
    # signal so a client can detect a clean shutdown vs network drop.
    yield "data: [DONE]\n\n"


class _LazyAgentGraph:
    """Defers ``build_graph()`` until the first ``ainvoke`` call.

    Compiling a langgraph ``StateGraph`` is ~80-150 ms — cheap for one
    /turn but expensive when 50+ unit tests construct ``create_app``
    in their fixtures. Tests that never exercise the W2 graph path
    therefore never pay the compile cost.

    Production callers (real /turn requests with W2 inputs) pay the
    compile once per process, on the first qualifying turn. The
    compiled graph is then reused for the rest of the process
    lifetime — same total cost as eager compilation, just shifted
    from app startup to first use.

    The class itself satisfies ``_AgentGraphLike`` structurally so it
    can be passed straight through to :class:`Orchestrator`.
    """

    __slots__ = ("_builder", "_compiled")

    def __init__(self, builder: Callable[[], _AgentGraphLike]) -> None:
        self._builder = builder
        self._compiled: _AgentGraphLike | None = None

    async def ainvoke(
        self, state: Any, config: Any = None
    ) -> dict[str, Any]:
        if self._compiled is None:
            self._compiled = self._builder()
        return await self._compiled.ainvoke(state, config)


def _build_evidence_retriever(settings: Settings) -> EvidenceRetriever | None:
    """Construct the W2 hybrid-RAG pipeline, or return None.

    Returns None when:

    * ``settings.evidence_retriever_enabled`` is False — typical for
      unit tests and for deployments without a guideline corpus.
    * The corpus index file is missing on disk — graceful degradation
      so a misconfigured deployment serves chart-question turns
      normally and only the evidence-retrieval path no-ops.

    When constructed, the pipeline pulls the SentenceTransformer
    encoder (~80 MB) and the cross-encoder reranker (~110 MB) into
    the Hugging Face cache on first ``encode()`` call. Construction
    itself does not download anything — but the very first
    evidence-query turn will pay that cost. Production callers should
    warm the model cache via the launch script if startup latency
    matters.
    """
    if not settings.evidence_retriever_enabled:
        return None
    if not settings.guidelines_index_path.is_file():
        logger.warning(
            "guideline corpus missing at %s; evidence retriever disabled",
            settings.guidelines_index_path,
        )
        return None
    chunks = load_corpus(settings.guidelines_index_path)
    encoder = SentenceTransformerEncoder()
    cross_encoder = SentenceTransformerCrossEncoder()
    return EvidenceRetriever(
        bm25=BM25Retriever(chunks),
        dense=DenseRetriever(chunks, encoder=encoder),
        merger=RRFMerger(),
        reranker=CrossEncoderReranker(cross_encoder),
    )


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
    search_notes_fetcher: SearchNotesFetcher | None = None,
    encounters_fetcher: EncountersFetcher | None = None,
    immunizations_fetcher: ImmunizationsFetcher | None = None,
    procedures_fetcher: ProceduresFetcher | None = None,
    breakglass_audit: BreakglassAuditTool | None = None,
    redis_storage: AgentRedisClient | None = None,
    langfuse_client: LangfuseClient | None = None,
    redis_client: _AppRedisProto | None = None,
    planner: Planner | None = None,
    truncator: SynthesisInputTruncator | None = None,
    data_quality: DataQualityChecker | None = None,
    pdf_renderer: PdfRenderer | None = None,
    document_bytes_fetcher: DocumentBytesFetcher | None = None,
    vision_extractor: VisionExtractor[Any] | None = None,
    evidence_retriever: EvidenceRetriever | None = None,
    agent_graph: _AgentGraphLike | None = None,
) -> FastAPI:
    """Construct the FastAPI application.

    Dependencies are injectable so tests can swap in fakes without
    touching the network or environment. `uvicorn agentforge.main:create_app
    --factory` is the production entry point.
    """
    settings = settings or get_settings()

    # Production path (`uvicorn ... --factory`) calls this without a
    # redis_client; the policy loader and AuthGateway visibility check
    # both gate on `redis_client is not None`, so leaving it None silently
    # disables sensitivity-policy enforcement on the droplet. Build one
    # from settings here so production matches the test wiring shape.
    # `redis.asyncio.Redis` satisfies both `_AppRedisProto` (this file)
    # and `_RedisProto` (storage/redis_client.py), so the same connection
    # pool can serve both consumers.
    if redis_client is None:
        redis_client = cast(
            _AppRedisProto,
            redis_async.from_url(settings.redis_url, decode_responses=False),
        )

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
    search_notes = search_notes_fetcher or SearchNotesFetcher(
        base_url=settings.openemr_base_url,
        gateway=auth_gateway,
    )
    encounters = encounters_fetcher or EncountersFetcher(
        base_url=settings.openemr_base_url,
        gateway=auth_gateway,
    )
    immunizations = immunizations_fetcher or ImmunizationsFetcher(
        base_url=settings.openemr_base_url,
    )
    procedures = procedures_fetcher or ProceduresFetcher(
        base_url=settings.openemr_base_url,
    )
    breakglass = breakglass_audit or BreakglassAuditTool(
        base_url=settings.openemr_base_url,
    )
    langfuse: LangfuseClient = langfuse_client or _build_langfuse(settings)

    # Default-on planner. Constructed against the same LLM client the
    # orchestrator uses so cost tracking, model selection, and any
    # future client-side instrumentation share one path. Tests can
    # inject a stub via the `planner=` kwarg to skip the real LLM.
    planner_instance = planner or Planner(llm=llm)

    # Default-on synthesis-input truncator. Held but not yet invoked
    # by the orchestrator (see DEVIATIONS.md 2026-05-02 — behavioral
    # integration deferred to the streaming refactor). Constructed
    # at startup because the tiktoken encoder is non-trivial to
    # instantiate per-request.
    truncator_instance = truncator or SynthesisInputTruncator()

    # Default-on data-quality checker (week1-gaps #7). Stateless
    # except for the injected clock; runs on every turn after the
    # tool loop closes and before the response is persisted.
    data_quality_instance = data_quality or DataQualityChecker(
        now=lambda: datetime.now(UTC),
    )

    # ------------------------------------------------------------------
    # W2 graph wiring (Task 1, MR 7). Each collaborator below is a
    # thin construction site behind an injection kwarg; tests pass
    # explicit overrides to skip the network / ML-model work.
    # ------------------------------------------------------------------

    # PdfRenderer is stateless (just a DPI knob); a single instance is
    # safe to share across requests. The /turn route uses it to render
    # uploaded PDF bytes into the per-page PNGs the graph's intake
    # extractor needs.
    pdf_renderer_instance = pdf_renderer or PdfRenderer()

    # DocumentBytesFetcher holds a long-lived httpx.AsyncClient
    # (10s timeout). Bound to the OpenEMR base URL so the /turn route
    # can resolve a request's ``document_id`` to raw bytes via the
    # JWT-validated ``InternalDocumentBytesController`` endpoint.
    document_bytes_fetcher_instance = document_bytes_fetcher or DocumentBytesFetcher(
        base_url=settings.openemr_base_url,
    )

    # VisionExtractor is built only when an Anthropic key is
    # configured. Construction does NOT call out (the AsyncAnthropic
    # client is lazy on first request), so an empty key would still
    # let construction succeed — but the FIRST extraction request
    # would crash. Surfacing the absence as ``None`` here means the
    # graph's intake-extractor node degrades to a clean no-op for
    # local-dev / test runs that have no key.
    vision_extractor_instance = vision_extractor
    if vision_extractor_instance is None and settings.anthropic_api_key:
        vision_extractor_instance = VisionExtractor(
            contract=INTAKE_CONTRACT,
            client=AsyncAnthropic(api_key=settings.anthropic_api_key),
        )

    # EvidenceRetriever build is gated on the feature flag + corpus
    # presence (see ``_build_evidence_retriever``). Tests pass
    # ``EVIDENCE_RETRIEVER_ENABLED=false`` to skip ML-model loads;
    # production deployments with the bundled corpus get a fully
    # wired retriever without any explicit env var.
    evidence_retriever_instance = (
        evidence_retriever
        if evidence_retriever is not None
        else _build_evidence_retriever(settings)
    )

    # Wrap ``build_graph`` in a lazy compiler so the langgraph compile
    # only fires on the first /turn that routes through the W2 path —
    # chart-question turns never trigger it, and unit-test fixtures
    # that construct ``create_app`` for non-W2 assertions stay fast.
    # Each worker dependency is captured at create_app time, so the
    # eventual compile sees the wiring this app started with.
    def _compile_graph() -> _AgentGraphLike:
        return cast(
            _AgentGraphLike,
            build_graph(
                planner_instance,
                vision_extractor=vision_extractor_instance,
                evidence_retriever=evidence_retriever_instance,
                synthesis_llm=llm,
                truncator=truncator_instance,
                data_quality_checker=data_quality_instance,
                langfuse=langfuse,
                domain_checker=None,
            ),
        )

    agent_graph_instance: _AgentGraphLike = (
        agent_graph if agent_graph is not None else _LazyAgentGraph(_compile_graph)
    )

    orchestrator = Orchestrator(
        llm=llm,
        demographics_fetcher=demographics,
        medications_fetcher=medications,
        problems_fetcher=problems,
        allergies_fetcher=allergies,
        labs_fetcher=labs,
        vitals_fetcher=vitals,
        notes_fetcher=notes,
        search_notes_fetcher=search_notes,
        encounters_fetcher=encounters,
        immunizations_fetcher=immunizations,
        procedures_fetcher=procedures,
        breakglass_audit=breakglass,
        # Domain substance checks (med name/dose match, lab tolerance, etc.)
        # are too strict against Claude's formatted output (markdown bold,
        # full drug names with form suffixes). Citation-existence check via
        # the index is the floor and is preserved. Re-enable substance
        # checks once normalization handles formatted output reliably.
        domain_constraints=None,
        verifier_enabled=settings.verifier_enabled,
        langfuse=langfuse,
        hmac_key=settings.hmac_key.encode("utf-8"),
        redis_storage=storage,
        memory=ConversationMemory(redis_storage=storage),
        planner=planner_instance,
        truncator=truncator_instance,
        data_quality=data_quality_instance,
        identity_guard_enabled=True,
        agent_graph=agent_graph_instance,
    )

    app.state.auth_gateway = auth_gateway
    app.state.orchestrator = orchestrator
    app.state.redis_storage = storage
    app.state.langfuse = langfuse
    app.state.redis_client = redis_client
    # Stashed for the /turn route handler (slice D), which translates
    # a request's ``document_id`` into the ``pdf_pages`` the graph
    # consumes by chaining fetcher → renderer.
    app.state.pdf_renderer = pdf_renderer_instance
    app.state.document_bytes_fetcher = document_bytes_fetcher_instance

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
        fetcher: Annotated[
            DocumentBytesFetcher, Depends(get_document_bytes_fetcher)
        ],
        renderer: Annotated[PdfRenderer, Depends(get_pdf_renderer)],
        response: Response,
    ) -> TurnResponse | StreamingResponse:
        # W2 inputs route through the LangGraph rather than the W1
        # iterative loop (see Orchestrator.turn). The graph path
        # produces a single final assistant string — streaming the
        # tokens incrementally is deferred (DEVIATIONS.md 2026-05-05),
        # so a request with W2 inputs gets the buffered JSON response
        # regardless of the streaming setting.
        has_w2_input = body.document_id is not None or body.evidence_query

        # Streaming path (week1-gaps Task #10). Off by default; production
        # flips ``STREAMING_ENABLED=true`` only after the verify-BEFORE-emit
        # gate ships in #13. The cost header is emitted as a "final"
        # SSE frame instead of an HTTP header because the cost isn't
        # known until the body completes — by which time the response
        # headers have already been sent.
        if settings.streaming_enabled and not has_w2_input:
            return StreamingResponse(
                _sse_stream(orchestrator, ctx, body),
                media_type="text/event-stream",
                # Cache-Control: no SSE intermediary should buffer.
                # X-Accel-Buffering: nginx-specific knob with the same
                # intent — suppresses proxy buffering so deltas reach
                # the client as they're emitted.
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # Resolve ``document_id`` to ``pdf_pages`` by chaining the
        # fetcher and the renderer. Errors map to HTTP statuses the
        # JS panel can act on:
        #   503 — sidecar can't reach OpenEMR (transport-level)
        #   502 — OpenEMR returned an error (auth, scope, missing)
        #   422 — the document is not a PDF or won't parse as one
        pdf_pages: list[RenderedPage] | None = None
        if body.document_id is not None:
            try:
                document = await fetcher.fetch(
                    document_id=body.document_id,
                    raw_token=ctx.raw_token,
                )
            except DocumentBytesFetchError as exc:
                if exc.status_code == 0:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Document fetch failed: sidecar unreachable.",
                    ) from exc
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": "Document fetch failed.",
                        # ``sidecar_upstream_status`` lets the panel
                        # distinguish 401/403/404 visually without
                        # exposing the raw upstream body.
                        "sidecar_upstream_status": exc.status_code,
                    },
                ) from exc

            if document.mimetype != "application/pdf":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Document {body.document_id} is not a PDF "
                        f"(mimetype: {document.mimetype})."
                    ),
                )

            try:
                pdf_pages = renderer.render_pages(document.content)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Document {body.document_id} could not be "
                        f"rendered as a PDF."
                    ),
                ) from exc

        if has_w2_input:
            # W2 path — pass the new kwargs through. The orchestrator
            # decides graph routing internally based on which inputs
            # are populated.
            reply = await orchestrator.turn(
                ctx,
                body.message,
                session_id=body.session_id,
                pdf_pages=pdf_pages,
                document_id=body.document_id,
                evidence_query=body.evidence_query,
            )
        else:
            # W1 path — byte-identical to the legacy contract so
            # existing chart-question fixtures (and any orchestrator
            # stub that doesn't accept the W2 kwargs) keep working.
            reply = await orchestrator.turn(
                ctx, body.message, session_id=body.session_id
            )
        # Surface accumulated LLM cost as a response header so the
        # PHP module can log it next to the user/pid for the request
        # (Week 1 Task #14). Read AFTER turn() returns and within the
        # same asyncio task so the ContextVar still holds this turn's
        # value. Six-decimal format because Anthropic's cheapest call
        # is ~$1e-5 — three decimals would round to zero.
        response.headers["X-Agent-Cost-USD"] = f"{get_turn_cost_usd():.6f}"
        # Emit Langfuse trace ID so the PHP proxy can log it alongside
        # the user/patient context for cross-system correlation. Only
        # set when a real trace was opened (NullLangfuseClient → None).
        trace_id = get_last_trace_id()
        if trace_id is not None:
            response.headers["X-Trace-Id"] = trace_id
        return TurnResponse(reply=reply, extraction=get_last_turn_extraction())

    return app
