"""Orchestrator → ExtractionPersister wiring (P1.1).

The orchestrator hooks the persister into the post-extract block of
``_run_graph_turn`` so each successful intake/lab graph turn lands
the extraction in OpenEMR. The wiring contract under test:

- A graph turn that produces an :class:`IntakeFormExtraction` calls
  ``persister.persist_intake`` exactly once with the extraction, the
  caller's ``patient_id`` / ``document_id``, and a freshly-minted
  internal JWT. The returned :class:`PersistedHandle` rides the per-
  turn ``_TURN_PERSISTED_VAR`` ContextVar so the BFF can surface it
  on :class:`AgentTurnResponse`.
- Same for a turn that produces a :class:`LabPdfExtraction` (lab
  controller). Dispatch is by ``isinstance`` on the extraction shape;
  the graph today only emits intake, but the wiring is forward-
  compatible with the lab worker.
- A turn with no extraction (e.g. evidence-only, or graph short-
  circuited) does not call the persister at all, and leaves the
  ContextVar at ``None``.
- A turn where the persister is absent (``None`` injected) silently
  skips persistence — keeps the test fixtures and local-dev flows
  that don't wire OpenEMR running unchanged.
- A persist failure logs at WARNING via PSR-3-style ``extra=`` kwargs
  but does NOT raise — the synthesis turn still returns the model's
  reply text so the user sees their answer. Persistence is best
  effort.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentforge.dashboard_auth.internal_jwt import InternalJwtMinter
from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.types import LLMResponse
from agentforge.orchestrator import (
    Orchestrator,
    get_last_persisted_handle,
)
from agentforge.persist import (
    ExtractionPersister,
    ExtractionPersistError,
    PersistedHandle,
)
from agentforge.schemas.citation import Citation, PageBBox, SourceType
from agentforge.schemas.intake import IntakeFormExtraction
from agentforge.schemas.lab import LabPdfExtraction
from agentforge.tools.attach_and_extract import RenderedPage


def _ctx(patient_id: int = 8) -> RequestContext:
    return RequestContext(
        user_id=42,
        patient_id=patient_id,
        username="dr.smith",
        role="clinician",
        breakglass_flag=False,
        breakglass_reason=None,
        sensitivity_clearances=frozenset(),
        raw_token="raw.jwt.token",
    )


def _rendered_page() -> RenderedPage:
    return RenderedPage(
        page_number=1,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=850,
        pixel_height=1100,
    )


def _intake_citation() -> Citation:
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="12",
        page_or_section="page 1",
        field_or_chunk_id="dob",
        quote_or_value="1972-04-12",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.2, bbox_confidence=0.9
        ),
    )


def _intake_extraction() -> IntakeFormExtraction:
    return IntakeFormExtraction(
        document_id=12,
        patient_id=8,
        chief_concern="visit",
        chief_concern_citation=_intake_citation(),
        extraction_confidence=0.9,
    )


def _lab_extraction() -> LabPdfExtraction:
    citation = Citation(
        source_type=SourceType.LAB_PDF,
        source_id="12",
        page_or_section="page 1",
        field_or_chunk_id="hgb",
        quote_or_value="13.2",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.2, bbox_confidence=0.9
        ),
    )
    from agentforge.schemas.lab import AbnormalFlag, LabValue

    return LabPdfExtraction(
        document_id=12,
        patient_id=8,
        values=[
            LabValue(
                test_name="hemoglobin",
                value="13.2",
                abnormal_flag=AbnormalFlag.NORMAL,
                citation=citation,
            ),
        ],
        extraction_confidence=0.9,
    )


class _RecordingGraph:
    """Stub graph that returns a configurable extraction in its result."""

    def __init__(
        self,
        *,
        extraction: Any,
        response_text: str = "graph reply",
    ) -> None:
        self._extraction = extraction
        self._response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(
        self, state: Any, config: Any = None
    ) -> dict[str, Any]:
        del config
        self.calls.append({"state": state})
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": self._response_text})
        return {**state, "messages": messages, "extraction_result": self._extraction}


def _build_orchestrator(
    *,
    agent_graph: Any,
    persister: ExtractionPersister | AsyncMock | None = None,
    jwt_minter: InternalJwtMinter | None = None,
) -> Orchestrator:
    llm = AsyncMock()
    llm.complete.return_value = LLMResponse(
        text="w1 (unused on graph route)",
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )
    return Orchestrator(
        llm=llm,
        demographics_fetcher=AsyncMock(),
        medications_fetcher=AsyncMock(),
        problems_fetcher=AsyncMock(),
        allergies_fetcher=AsyncMock(),
        labs_fetcher=AsyncMock(),
        vitals_fetcher=AsyncMock(),
        notes_fetcher=AsyncMock(),
        search_notes_fetcher=AsyncMock(),
        encounters_fetcher=AsyncMock(),
        immunizations_fetcher=AsyncMock(),
        procedures_fetcher=AsyncMock(),
        agent_graph=agent_graph,
        persister=persister,
        jwt_minter=jwt_minter,
    )


def _stub_minter() -> InternalJwtMinter:
    """A minter that produces a stable token without needing PyJWT signing."""
    minter = MagicMock(spec=InternalJwtMinter)
    minter.mint.return_value = "minted.internal.jwt"
    return minter


# ---------------------------------------------------------------------------
# intake dispatch
# ---------------------------------------------------------------------------


async def test_intake_extraction_persists_and_stashes_handle() -> None:
    persister = AsyncMock(spec=ExtractionPersister)
    persister.persist_intake.return_value = PersistedHandle(
        resource_id="117", kind="questionnaire_response"
    )
    extraction = _intake_extraction()
    graph = _RecordingGraph(extraction=extraction)
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=persister,
        jwt_minter=_stub_minter(),
    )

    reply = await orch.turn(
        _ctx(),
        "look at this intake form",
        pdf_pages=[_rendered_page()],
        document_id=12,
    )

    assert reply == "graph reply"
    persister.persist_intake.assert_awaited_once()
    call = persister.persist_intake.await_args
    assert call is not None
    args, kwargs = call.args, call.kwargs
    # Pydantic instance is the first positional, kwargs carry the IDs + JWT.
    assert isinstance(args[0], IntakeFormExtraction)
    assert kwargs == {
        "patient_id": 8,
        "document_id": 12,
        "internal_jwt": "minted.internal.jwt",
    }
    persister.persist_lab.assert_not_called()

    handle = get_last_persisted_handle()
    assert handle is not None
    assert handle.resource_id == "117"
    assert handle.kind == "questionnaire_response"


# ---------------------------------------------------------------------------
# lab dispatch
# ---------------------------------------------------------------------------


async def test_lab_extraction_persists_via_lab_method() -> None:
    persister = AsyncMock(spec=ExtractionPersister)
    persister.persist_lab.return_value = PersistedHandle(
        resource_id="42", kind="procedure_order"
    )
    extraction = _lab_extraction()
    graph = _RecordingGraph(extraction=extraction)
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=persister,
        jwt_minter=_stub_minter(),
    )

    await orch.turn(
        _ctx(),
        "look at this lab pdf",
        pdf_pages=[_rendered_page()],
        document_id=12,
    )

    persister.persist_lab.assert_awaited_once()
    persister.persist_intake.assert_not_called()
    handle = get_last_persisted_handle()
    assert handle is not None
    assert handle.kind == "procedure_order"
    assert handle.resource_id == "42"


# ---------------------------------------------------------------------------
# no extraction → no persist
# ---------------------------------------------------------------------------


async def test_no_extraction_does_not_call_persister() -> None:
    persister = AsyncMock(spec=ExtractionPersister)
    graph = _RecordingGraph(extraction=None)
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=persister,
        jwt_minter=_stub_minter(),
    )

    await orch.turn(
        _ctx(),
        "tell me about the chart",
        evidence_query="hyperlipidemia management",
    )

    persister.persist_intake.assert_not_called()
    persister.persist_lab.assert_not_called()
    assert get_last_persisted_handle() is None


# ---------------------------------------------------------------------------
# missing persister → silent skip
# ---------------------------------------------------------------------------


async def test_missing_persister_is_silent_skip() -> None:
    graph = _RecordingGraph(extraction=_intake_extraction())
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=None,
        jwt_minter=None,
    )
    # The hook must not crash even though no persister is wired.
    reply = await orch.turn(
        _ctx(),
        "intake",
        pdf_pages=[_rendered_page()],
        document_id=12,
    )
    assert reply == "graph reply"
    assert get_last_persisted_handle() is None


# ---------------------------------------------------------------------------
# persist failure → log + continue
# ---------------------------------------------------------------------------


async def test_persist_failure_logs_warning_but_turn_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    persister = AsyncMock(spec=ExtractionPersister)
    persister.persist_intake.side_effect = ExtractionPersistError(
        status_code=500, message="upstream boom"
    )
    graph = _RecordingGraph(extraction=_intake_extraction())
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=persister,
        jwt_minter=_stub_minter(),
    )

    with caplog.at_level(logging.WARNING):
        reply = await orch.turn(
            _ctx(),
            "intake",
            pdf_pages=[_rendered_page()],
            document_id=12,
        )

    # Synthesis text still surfaces — that's the load-bearing invariant.
    assert reply == "graph reply"
    persister.persist_intake.assert_awaited_once()
    # No handle stashed; the orchestrator did not get one back.
    assert get_last_persisted_handle() is None

    # PSR-3 style: structured kwargs, no string interpolation of PHI.
    matched = [r for r in caplog.records if "persist" in r.getMessage().lower()]
    assert matched, "expected a persist-failure warning log"
    record = matched[0]
    # `status_code` and the IDs ride as structured fields, not in
    # the message body. The message is a fixed string.
    assert record.levelno == logging.WARNING
    extras = {
        k: v
        for k, v in record.__dict__.items()
        if k in ("status_code", "patient_id", "document_id")
    }
    assert extras == {"status_code": 500, "patient_id": 8, "document_id": 12}


# ---------------------------------------------------------------------------
# missing document_id → no persist
# ---------------------------------------------------------------------------


async def test_persist_skipped_when_document_id_missing() -> None:
    """Defensive: an extraction without an associated document_id can't
    pass the controller's triple-check. Skip persistence and log a warning
    rather than POSTing a payload guaranteed to 400."""
    persister = AsyncMock(spec=ExtractionPersister)
    graph = _RecordingGraph(extraction=_intake_extraction())
    orch = _build_orchestrator(
        agent_graph=graph,
        persister=persister,
        jwt_minter=_stub_minter(),
    )

    await orch.turn(
        _ctx(),
        "intake",
        pdf_pages=[_rendered_page()],
        document_id=None,
    )

    persister.persist_intake.assert_not_called()
    persister.persist_lab.assert_not_called()
    assert get_last_persisted_handle() is None
