"""P1.2 — doc_type plumbing through the BFF turn route, orchestrator,
and graph dispatch.

The W2 graph's intake-extractor node was wired to a single
``VisionExtractor`` built around ``INTAKE_CONTRACT``. After T38.15
opened the upload path to ``lab_pdf`` documents, a turn that carries
a lab PDF still routed through the intake contract — wrong tool name,
wrong schema. These tests gate the fix:

* ``DocumentType`` enum carries the two supported variants.
* ``AgentTurnRequest`` accepts ``doc_type`` and rejects bogus values.
* ``Orchestrator.turn`` plumbs ``doc_type`` into ``AgentState``.
* ``intake_extractor_node`` dispatches on ``state["doc_type"]`` to the
  matching extractor (intake vs lab), defaulting to intake when unset
  for back-compat.
* ``build_graph`` accepts both extractor kwargs and wires them into
  the dispatch.

Tests intentionally avoid hitting Anthropic / langgraph internals —
the dispatch logic lives in the node, so node-level tests suffice for
the meat. End-to-end goes through ``build_graph`` with stub extractors
to confirm the wiring round-trips state correctly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from agentforge.dashboard_auth.turn_route import AgentTurnRequest
from agentforge.orchestrator.graph import (
    HANDOFF_START_NODE,
    AgentState,
    DocumentType,
    RouteDecision,
    build_graph,
    intake_extractor_node,
)
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.schemas.citation import (
    Citation as W2Citation,
)
from agentforge.schemas.citation import (
    PageBBox,
    SourceType,
)
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.schemas.lab import AbnormalFlag, LabPdfExtraction, LabValue
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)

# ---------------------------------------------------------------------------
# Stubs — local copies so tests don't depend on the order of imports in
# test_orchestrator_graph.py. Mirror the StubVisionExtractor / StubPlanner
# patterns there.
# ---------------------------------------------------------------------------


class _StubPlanner:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan
        self.calls: list[str] = []

    async def plan(self, user_message: str) -> Plan:
        self.calls.append(user_message)
        return self._plan


class _StubIntakeExtractor:
    def __init__(self, result: VisionExtractionResult[IntakeFormExtraction]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
    ) -> VisionExtractionResult[IntakeFormExtraction]:
        self.calls.append(
            {"pages": pages, "document_id": document_id, "patient_id": patient_id}
        )
        return self._result


class _StubLabExtractor:
    def __init__(self, result: VisionExtractionResult[LabPdfExtraction]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        *,
        pages: list[RenderedPage],
        document_id: int,
        patient_id: int,
    ) -> VisionExtractionResult[LabPdfExtraction]:
        self.calls.append(
            {"pages": pages, "document_id": document_id, "patient_id": patient_id}
        )
        return self._result


def _rendered_page() -> RenderedPage:
    return RenderedPage(
        page_number=1,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=850,
        pixel_height=1100,
    )


def _starter_state(
    *,
    doc_type: DocumentType | None,
    document_id: int = 42,
    patient_id: int = 7,
) -> AgentState:
    return AgentState(
        messages=[{"role": "user", "content": "hello"}],
        tool_results={},
        route_decision=None,
        route_reason="",
        iteration=0,
        extraction_result=None,
        evidence_chunks=[],
        document_id=document_id,
        patient_id=patient_id,
        pdf_pages=[_rendered_page()],
        query="",
        langfuse_trace=None,
        last_node=HANDOFF_START_NODE,
        doc_type=doc_type,
    )


def _intake_result(
    document_id: int = 42,
    patient_id: int = 7,
) -> VisionExtractionResult[IntakeFormExtraction]:
    return VisionExtractionResult(
        extraction=IntakeFormExtraction(
            document_id=document_id,
            patient_id=patient_id,
            extraction_confidence=0.9,
        ),
        model="claude-test",
        input_tokens=100,
        output_tokens=50,
        cost_usd=None,
    )


def _lab_citation() -> W2Citation:
    return W2Citation(
        source_type=SourceType.LAB_PDF,
        source_id="42",
        page_or_section="page 1",
        field_or_chunk_id="hba1c",
        quote_or_value="6.8%",
        page_bbox=PageBBox(
            page=1,
            x0=0.1,
            y0=0.1,
            x1=0.5,
            y1=0.2,
            bbox_confidence=0.9,
        ),
    )


def _lab_result(
    document_id: int = 42,
    patient_id: int = 7,
) -> VisionExtractionResult[LabPdfExtraction]:
    return VisionExtractionResult(
        extraction=LabPdfExtraction(
            document_id=document_id,
            patient_id=patient_id,
            values=[
                LabValue(
                    test_name="HbA1c",
                    value="6.8",
                    unit="%",
                    collection_date=date(2024, 1, 1),
                    abnormal_flag=AbnormalFlag.NORMAL,
                    citation=_lab_citation(),
                )
            ],
            extraction_confidence=0.9,
        ),
        model="claude-test",
        input_tokens=100,
        output_tokens=50,
        cost_usd=None,
    )


# ---------------------------------------------------------------------------
# DocumentType enum
# ---------------------------------------------------------------------------


class TestDocumentTypeEnum:
    def test_values_match_wire_strings(self) -> None:
        # The wire format the BFF accepts is the closed set
        # ``intake_form`` | ``lab_pdf``. The enum's string values must
        # match exactly so Pydantic round-trips request bodies through
        # the enum without a separate translation layer.
        assert DocumentType.INTAKE_FORM.value == "intake_form"
        assert DocumentType.LAB_PDF.value == "lab_pdf"


# ---------------------------------------------------------------------------
# AgentTurnRequest — schema acceptance
# ---------------------------------------------------------------------------


class TestAgentTurnRequestDocType:
    def test_accepts_lab_pdf(self) -> None:
        # Pydantic accepts the wire-string form ("lab_pdf") and coerces
        # to the enum on parse — that's the request body shape the
        # dashboard actually sends. Drive ``model_validate`` directly
        # so we exercise the enum-coercion path mypy can't see through.
        body = AgentTurnRequest.model_validate(
            {
                "message": "Summarize this lab report.",
                "patient_uuid": "patient-uuid-1",
                "document_id": "42",
                "doc_type": "lab_pdf",
            }
        )
        assert body.doc_type == DocumentType.LAB_PDF

    def test_accepts_intake_form(self) -> None:
        body = AgentTurnRequest.model_validate(
            {
                "message": "Confirm intake fields.",
                "patient_uuid": "patient-uuid-1",
                "document_id": "42",
                "doc_type": "intake_form",
            }
        )
        assert body.doc_type == DocumentType.INTAKE_FORM

    def test_doc_type_optional(self) -> None:
        # Back-compat: existing callers don't send doc_type. Default
        # ``None`` keeps the orchestrator on the intake fall-through.
        body = AgentTurnRequest(
            message="Hi",
            patient_uuid="patient-uuid-1",
        )
        assert body.doc_type is None

    def test_rejects_bogus_doc_type(self) -> None:
        # Drive through ``model_validate`` so the closed-set enforcement
        # is exercised at the JSON-parse boundary the BFF actually hits
        # (FastAPI uses the same path under the hood).
        with pytest.raises(ValidationError):
            AgentTurnRequest.model_validate(
                {
                    "message": "Hi",
                    "patient_uuid": "patient-uuid-1",
                    "document_id": "42",
                    "doc_type": "malware_pdf",
                }
            )


# ---------------------------------------------------------------------------
# intake_extractor_node — dispatch on state["doc_type"]
# ---------------------------------------------------------------------------


class TestIntakeExtractorNodeDispatchesByDocType:
    @pytest.mark.asyncio
    async def test_lab_pdf_calls_lab_extractor(self) -> None:
        intake = _StubIntakeExtractor(_intake_result())
        lab = _StubLabExtractor(_lab_result())

        state = _starter_state(doc_type=DocumentType.LAB_PDF)

        update = await intake_extractor_node(
            state,
            extractor=intake,
            lab_extractor=lab,
        )

        assert intake.calls == []
        assert len(lab.calls) == 1
        assert isinstance(update["extraction_result"], LabPdfExtraction)

    @pytest.mark.asyncio
    async def test_intake_form_calls_intake_extractor(self) -> None:
        intake = _StubIntakeExtractor(_intake_result())
        lab = _StubLabExtractor(_lab_result())

        state = _starter_state(doc_type=DocumentType.INTAKE_FORM)

        update = await intake_extractor_node(
            state,
            extractor=intake,
            lab_extractor=lab,
        )

        assert lab.calls == []
        assert len(intake.calls) == 1
        assert isinstance(update["extraction_result"], IntakeFormExtraction)

    @pytest.mark.asyncio
    async def test_doc_type_none_defaults_to_intake(self) -> None:
        # Back-compat regression guard. When the BFF doesn't carry
        # doc_type (existing UI paths), the node must behave exactly
        # as it did pre-fix and route through the intake extractor.
        intake = _StubIntakeExtractor(_intake_result())
        lab = _StubLabExtractor(_lab_result())

        state = _starter_state(doc_type=None)

        update = await intake_extractor_node(
            state,
            extractor=intake,
            lab_extractor=lab,
        )

        assert lab.calls == []
        assert len(intake.calls) == 1
        assert isinstance(update["extraction_result"], IntakeFormExtraction)

    @pytest.mark.asyncio
    async def test_lab_pdf_with_no_lab_extractor_skips(self) -> None:
        # When a turn carries doc_type=lab_pdf but the build site
        # forgot to wire the lab extractor, fail-skip cleanly rather
        # than calling the wrong extractor. Same idempotency contract
        # as the missing-document_id path.
        intake = _StubIntakeExtractor(_intake_result())

        state = _starter_state(doc_type=DocumentType.LAB_PDF)

        update = await intake_extractor_node(
            state,
            extractor=intake,
            lab_extractor=None,
        )

        assert intake.calls == []
        assert update == {"last_node": RouteDecision.INTAKE_EXTRACTOR.value}


# ---------------------------------------------------------------------------
# build_graph — both extractors plumb through to the node
# ---------------------------------------------------------------------------


class TestBuildGraphAcceptsBothExtractors:
    @pytest.mark.asyncio
    async def test_lab_pdf_state_routes_to_lab_extractor(self) -> None:
        # End-to-end through the compiled graph with a stubbed
        # synthesizer. The graph must call the lab extractor when
        # state["doc_type"] is LAB_PDF — even though the legacy
        # node name is still "intake-extractor".
        from agentforge.llm.types import LLMResponse, Message, ToolSpec

        class _StubSynthLLM:
            async def complete(
                self,
                system: str,
                messages: list[Message],
                tools: list[ToolSpec] | None = None,
                max_tokens: int = 1024,
                temperature: float = 1.0,
            ) -> LLMResponse:
                return LLMResponse(
                    text="ok.",
                    stop_reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                )

        planner = _StubPlanner(
            Plan(use_case=UseCase.ADMIT_SYNTHESIS, tool_calls=(), parallel_batches=())
        )
        intake = _StubIntakeExtractor(_intake_result())
        lab = _StubLabExtractor(_lab_result())

        graph = build_graph(
            planner,
            vision_extractor=intake,
            vision_extractor_lab=lab,
            synthesis_llm=_StubSynthLLM(),
        )

        starter = _starter_state(doc_type=DocumentType.LAB_PDF)
        result = await graph.ainvoke(starter)

        assert intake.calls == []
        assert len(lab.calls) == 1
        assert isinstance(result["extraction_result"], LabPdfExtraction)


# ---------------------------------------------------------------------------
# BFF /api/agent/turn — doc_type forwards to orchestrator.turn
# ---------------------------------------------------------------------------


class _DocTypeCapturingOrchestrator:
    """Stand-in orchestrator that captures the kwargs the BFF passed.

    Asserts the round-trip: AgentTurnRequest.doc_type → request body →
    Pydantic parse → ``orchestrator.turn(doc_type=...)``.
    """

    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.captured: dict[str, Any] = {}

    async def turn(
        self,
        ctx: Any,
        user_message: str,
        *,
        session_id: str | None = None,
        pdf_pages: Any = None,
        document_id: int | None = None,
        doc_type: DocumentType | None = None,
        **_: Any,
    ) -> str:
        self.captured = {
            "ctx": ctx,
            "user_message": user_message,
            "session_id": session_id,
            "pdf_pages": pdf_pages,
            "document_id": document_id,
            "doc_type": doc_type,
        }
        return self.reply


@pytest.mark.asyncio
async def test_bff_forwards_doc_type_lab_pdf_to_orchestrator() -> None:
    # Reuse the existing test_agent_turn_route fixtures — they already
    # set up session store + AuthGateway + httpx.MockTransport on /me
    # and /patient_pid. We just swap the orchestrator stub for one that
    # captures ``doc_type``.
    import datetime as dt
    from unittest.mock import AsyncMock

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentforge.config import Settings
    from agentforge.dashboard_auth import SessionStore
    from agentforge.dashboard_auth.internal_jwt import InternalJwtMinter
    from agentforge.dashboard_auth.openemr_me import OpenEMRMeFetcher
    from agentforge.dashboard_auth.openemr_patient_pid import (
        OpenEMRPatientPidFetcher,
    )
    from agentforge.dashboard_auth.turn_route import make_agent_turn_router
    from agentforge.gateway.auth_gateway import AuthGateway

    secret = "a-very-long-test-secret-that-is-at-least-32b"
    cookie_name = "agentforge_session"
    base = "https://openemr.example"

    class _Clock:
        def now(self) -> dt.datetime:
            return dt.datetime.now(dt.UTC)

    storage: dict[str, str] = {}
    redis_mock = AsyncMock()

    async def get(key: str) -> str | None:
        return storage.get(key)

    async def setex(key: str, _ttl: int, value: str) -> None:
        storage[key] = value

    async def delete(*keys: str) -> int:
        n = 0
        for k in keys:
            if k in storage:
                del storage[k]
                n += 1
        return n

    redis_mock.get.side_effect = get
    redis_mock.setex.side_effect = setex
    redis_mock.delete.side_effect = delete

    settings = Settings(
        anthropic_api_key="x",
        jwt_secret=secret,
        dashboard_session_cookie_name=cookie_name,
        redis_url="redis://localhost:6379/0",
        hmac_key="dGVzdC1obWFjLWtleS0zMi1ieXRlcy1zZWNyZXQtdGVzdGluZw==",
    )
    session_store = SessionStore(
        redis_client=redis_mock,
        session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        pending_ttl_seconds=settings.dashboard_pending_auth_ttl_seconds,
    )

    def me_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user_id": 17, "username": "admin", "role": "Administrators"},
        )

    def pid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pid": 42})

    me_fetcher = OpenEMRMeFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(me_handler)),
        base_url=base,
        jwt_secret=secret,
        clock=_Clock(),
    )
    pid_fetcher = OpenEMRPatientPidFetcher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(pid_handler)),
        base_url=base,
        jwt_secret=secret,
        clock=_Clock(),
    )
    minter = InternalJwtMinter(jwt_secret=secret, clock=_Clock())
    gateway = AuthGateway(jwt_secret=secret, redis_client=None)

    orch = _DocTypeCapturingOrchestrator()
    router = make_agent_turn_router(
        settings=settings,
        session_store=session_store,
        me_fetcher=me_fetcher,
        patient_pid_fetcher=pid_fetcher,
        jwt_minter=minter,
        auth_gateway=gateway,
        orchestrator=orch,  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(router)

    session = await session_store.create_session(
        sub="oauth-sub",
        access_token="access-tok",
        expires_at=9_999_999_999.0,
        fhir_user=f"{base}/fhir/Practitioner/abc-uuid",
    )

    with TestClient(app) as client:
        client.cookies.set(cookie_name, session.session_id)
        resp = client.post(
            "/api/agent/turn",
            json={
                "message": "summarize this lab report",
                "patient_uuid": "patient-uuid",
                "doc_type": "lab_pdf",
            },
        )

    assert resp.status_code == 200
    assert orch.captured["doc_type"] is DocumentType.LAB_PDF

