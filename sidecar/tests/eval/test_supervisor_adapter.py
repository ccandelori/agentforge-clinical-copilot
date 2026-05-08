"""Tests for the production W2 SupervisorAdapter (follow-up to Task 18).

The adapter is the integration boundary between the eval surface (which
expects ``Callable[[EvalCase], SupervisorOutput]``) and the LangGraph
orchestrator. These tests pin the contract:

* The adapter shapes the graph result into a SupervisorOutput.
* Evidence-only cases drive the retrieval path.
* Document-attached cases drive the extraction path.
* The route-decision trail surfaces in ``output.logs``.
* Empty-evidence cases degrade gracefully (no crash, sensible output).

Every test mocks the LLM, vision extractor, and retriever — no
Anthropic spend, no model loads. The adapter's job is *plumbing*; the
real LLM behaviour is verified by the manual baseline regen run.
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentforge.eval.supervisor_adapter import (
    DocumentFixtureResolver,
    SupervisorAdapter,
    SupervisorAdapterDeps,
)
from agentforge.llm.types import LLMResponse, Message, ToolSpec
from agentforge.orchestrator.planner import Plan, UseCase
from agentforge.rag.evidence_retriever import RetrievalStats
from agentforge.rag.types import GuidelineChunk, RetrievalResult
from agentforge.schemas.citation import (
    Citation as W2Citation,
)
from agentforge.schemas.citation import (
    PageBBox,
    SourceType,
)
from agentforge.schemas.intake import (
    AllergyEntry,
    Demographic,
    IntakeFormExtraction,
    MedicationEntry,
)
from agentforge.tools.attach_and_extract import (
    RenderedPage,
    VisionExtractionResult,
)
from tests.eval.gate.runner_w2 import SupervisorOutput
from tests.eval.harness import EvalCase, EvalCategory


# ---------------------------------------------------------------------------
# Stubs — minimal fakes that satisfy the graph's Protocol-typed deps.
# ---------------------------------------------------------------------------


class _StubPlanner:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    async def plan(self, user_message: str) -> Plan:
        return self._plan


class _StubVisionExtractor:
    """Fakes ``VisionExtractor[IntakeFormExtraction]``.

    Returns a pre-built ``VisionExtractionResult`` whose extraction
    carries a single INTAKE_FORM citation so the harness's citation_present
    check is satisfied through the structured-citation path.
    """

    def __init__(
        self, result: VisionExtractionResult[IntakeFormExtraction]
    ) -> None:
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
            {
                "pages": pages,
                "document_id": document_id,
                "patient_id": patient_id,
            }
        )
        return self._result


class _StubEvidenceRetriever:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self._results = results
        self.calls: list[str] = []

    async def retrieve(
        self, query: str, *, top_k: int = 5
    ) -> list[RetrievalResult]:
        self.calls.append(query)
        return list(self._results)

    async def retrieve_with_stats(
        self, query: str, *, top_k: int = 5
    ) -> RetrievalStats:
        self.calls.append(query)
        return RetrievalStats(
            results=list(self._results),
            bm25_count=len(self._results),
            dense_count=len(self._results),
            post_rerank_count=len(self._results),
        )


class _StubSynthesisLLM:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 1.0,
    ) -> LLMResponse:
        self.calls.append(
            {"system": system, "messages": messages, "max_tokens": max_tokens}
        )
        return LLMResponse(
            text=self._response_text,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=10,
        )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _intake_citation(field_id: str = "primary_complaint") -> W2Citation:
    return W2Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="42",
        page_or_section="page 1",
        field_or_chunk_id=field_id,
        quote_or_value="chest pain",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.5, y1=0.2, bbox_confidence=0.9
        ),
    )


def _intake_extraction_result(
    document_id: int = 42, patient_id: int = 1
) -> VisionExtractionResult[IntakeFormExtraction]:
    return VisionExtractionResult(
        extraction=IntakeFormExtraction(
            document_id=document_id,
            patient_id=patient_id,
            extraction_confidence=0.9,
            chief_concern="chest pain",
            chief_concern_citation=_intake_citation("chief_concern"),
        ),
        model="claude-test",
        input_tokens=100,
        output_tokens=50,
        cost_usd=None,
    )


def _retrieval_result(chunk_id: str = "c1") -> RetrievalResult:
    chunk = GuidelineChunk.from_index_entry(
        doc_id="ada-2024",
        section="9.1",
        version="2024",
        chunk_id=chunk_id,
        text="A1C target for most adults is <7%.",
        token_count=10,
        source_path="ada-2024.pdf",
    )
    return RetrievalResult(chunk=chunk, score=0.9)


def _rendered_page() -> RenderedPage:
    return RenderedPage(
        page_number=1,
        png_bytes=b"\x89PNG\r\n\x1a\n",
        pixel_width=850,
        pixel_height=1100,
    )


def _evidence_case() -> EvalCase:
    return EvalCase(
        id="w2_ev_test",
        category=EvalCategory.EVIDENCE_RETRIEVAL,
        patient_id=1,
        query="What is the A1C target for non-pregnant adults?",
        expected_behavior="Cites the ADA glycemic targets chunk.",
    )


def _extraction_case() -> EvalCase:
    return EvalCase(
        id="w2_ext_test",
        category=EvalCategory.EXTRACTION,
        patient_id=1,
        query="Extract intake form fields (p01-chen-intake-typed.pdf).",
        expected_behavior="Returns demographics + chief complaint.",
    )


class _ConstantResolver:
    """Returns the same rendered pages for every case, regardless of query.

    Tests that exercise the document-attached path use this resolver
    so they don't need real PDF bytes on disk.
    """

    def __init__(self, pages: list[RenderedPage] | None) -> None:
        self._pages = pages
        self.calls: list[str] = []

    def resolve(self, case: EvalCase) -> tuple[list[RenderedPage], int | None]:
        self.calls.append(case.id)
        if self._pages is None:
            return ([], None)
        return (list(self._pages), 42)


def _make_deps(
    *,
    plan: Plan | None = None,
    vision_result: VisionExtractionResult[IntakeFormExtraction] | None = None,
    retrieval_results: list[RetrievalResult] | None = None,
    response_text: str = (
        "The A1C target is <7% [guideline #c1]."
    ),
    document_resolver: DocumentFixtureResolver | None = None,
) -> tuple[SupervisorAdapterDeps, dict[str, Any]]:
    """Build deps + return handles to the stubs for assertion."""
    used_plan = plan if plan is not None else Plan(
        use_case=UseCase.ADMIT_SYNTHESIS,
        tool_calls=(),
        parallel_batches=(),
    )
    extractor = _StubVisionExtractor(
        vision_result if vision_result is not None else _intake_extraction_result()
    )
    retriever = _StubEvidenceRetriever(
        retrieval_results if retrieval_results is not None else [
            _retrieval_result()
        ]
    )
    llm = _StubSynthesisLLM(response_text)
    resolver = document_resolver if document_resolver is not None else _ConstantResolver(None)

    deps = SupervisorAdapterDeps(
        planner=_StubPlanner(used_plan),
        vision_extractor=extractor,
        evidence_retriever=retriever,
        synthesis_llm=llm,
        document_resolver=resolver,
    )
    handles = {
        "extractor": extractor,
        "retriever": retriever,
        "llm": llm,
        "resolver": resolver,
    }
    return deps, handles


# ---------------------------------------------------------------------------
# 1. Contract — adapter is callable and returns a SupervisorOutput
# ---------------------------------------------------------------------------


class TestAdapterContract:
    @pytest.mark.asyncio
    async def test_adapter_is_callable_and_returns_supervisor_output(
        self,
    ) -> None:
        # Evidence-only case → adapter drives the retrieval path → returns
        # a fully-populated SupervisorOutput. This is the bare contract;
        # downstream tests pin specific fields.
        deps, _ = _make_deps()
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_evidence_case())

        assert isinstance(output, SupervisorOutput)
        assert output.response  # non-empty assistant text
        # structured_citation_payload must be a dict (Pydantic-validatable);
        # ProgrammaticChecks runs Citation.model_validate over it.
        assert isinstance(output.structured_citation_payload, dict)
        assert isinstance(output.structured_citations, tuple)
        assert isinstance(output.logs, tuple)


# ---------------------------------------------------------------------------
# 2. Evidence-only path
# ---------------------------------------------------------------------------


class TestEvidenceOnlyPath:
    @pytest.mark.asyncio
    async def test_evidence_query_drives_retriever(self) -> None:
        deps, handles = _make_deps()
        adapter = SupervisorAdapter(deps=deps)
        case = _evidence_case()

        await adapter(case)

        retriever = handles["retriever"]
        assert isinstance(retriever, _StubEvidenceRetriever)
        # Retriever was invoked with the case's query.
        assert retriever.calls == [case.query]

    @pytest.mark.asyncio
    async def test_evidence_chunks_become_structured_citations(self) -> None:
        # Each retrieved chunk carries a GUIDELINE Citation; the adapter
        # surfaces those on the output so the harness's structured-citation
        # check sees them.
        deps, _ = _make_deps()
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_evidence_case())

        assert len(output.structured_citations) >= 1
        guideline_citations = [
            c for c in output.structured_citations
            if c.source_type == SourceType.GUIDELINE
        ]
        assert guideline_citations, (
            f"expected at least one GUIDELINE citation, got types: "
            f"{[c.source_type for c in output.structured_citations]}"
        )

    @pytest.mark.asyncio
    async def test_empty_retrieval_does_not_crash(self) -> None:
        # Graceful degradation: zero evidence chunks → adapter still
        # returns a well-formed SupervisorOutput, no crash. The response
        # may be empty-of-citations but the *shape* is honest.
        deps, _ = _make_deps(retrieval_results=[])
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_evidence_case())

        assert isinstance(output, SupervisorOutput)
        # No structured citations from retrieval → the structured tuple
        # is empty (the adapter never fabricates one).
        assert all(
            c.source_type != SourceType.GUIDELINE
            for c in output.structured_citations
        )


# ---------------------------------------------------------------------------
# 3. Document-attached path
# ---------------------------------------------------------------------------


class TestDocumentAttachedPath:
    @pytest.mark.asyncio
    async def test_document_resolver_drives_extractor(self) -> None:
        # When the resolver returns rendered pages + a document_id, the
        # vision extractor must run with those pages.
        resolver = _ConstantResolver([_rendered_page()])
        deps, handles = _make_deps(document_resolver=resolver)
        adapter = SupervisorAdapter(deps=deps)

        await adapter(_extraction_case())

        extractor = handles["extractor"]
        assert isinstance(extractor, _StubVisionExtractor)
        assert len(extractor.calls) == 1
        assert extractor.calls[0]["document_id"] == 42

    @pytest.mark.asyncio
    async def test_extraction_citations_surface_on_output(self) -> None:
        # Citation fields on the IntakeFormExtraction (chief_concern,
        # demographics, etc.) must propagate to output.structured_citations
        # so the harness's citation_present check passes.
        resolver = _ConstantResolver([_rendered_page()])
        deps, _ = _make_deps(document_resolver=resolver)
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_extraction_case())

        intake_citations = [
            c for c in output.structured_citations
            if c.source_type == SourceType.INTAKE_FORM
        ]
        assert intake_citations, (
            "extraction case must surface at least one INTAKE_FORM "
            "citation from the IntakeFormExtraction"
        )


# ---------------------------------------------------------------------------
# 4. Route-decision trail
# ---------------------------------------------------------------------------


class TestRouteDecisionLogging:
    @pytest.mark.asyncio
    async def test_route_decisions_appear_in_logs(self) -> None:
        # Each supervisor → worker handoff during the turn must surface
        # in output.logs as a stable, parseable string. The harness's
        # PHI sweep walks logs; the eval baseline regen reads them too.
        deps, _ = _make_deps()
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_evidence_case())

        # At minimum: one handoff per iteration the supervisor ran.
        # The exact string format is adapter-private; the test pins the
        # invariant ("at least one log line", "logs include the
        # synthesize step") rather than a literal.
        assert len(output.logs) >= 1
        joined = "\n".join(output.logs)
        assert "synthesize" in joined.lower(), (
            f"expected 'synthesize' route to appear in logs; got: {output.logs}"
        )

    @pytest.mark.asyncio
    async def test_logs_carry_no_phi_digit_runs(self) -> None:
        # The harness's check_no_phi_in_logs sweeps for SSN / phone /
        # 8-10 digit MRN runs. The adapter's logs must never carry such
        # patterns (route decisions are categorical strings, not IDs).
        # If a future change embeds raw IDs we want the test to catch it.
        import re

        deps, _ = _make_deps()
        adapter = SupervisorAdapter(deps=deps)

        output = await adapter(_evidence_case())

        for line in output.logs:
            assert not re.search(r"\b\d{8,10}\b", line), (
                f"log line carries digit-shaped PHI: {line!r}"
            )


# ---------------------------------------------------------------------------
# 5. Resolver protocol
# ---------------------------------------------------------------------------


class TestDocumentFixtureResolver:
    def test_protocol_is_satisfied_by_minimal_callable(self) -> None:
        # The resolver is a Protocol — anything with a ``resolve(case)``
        # method that returns ``(list[RenderedPage], int | None)``
        # satisfies it. This anchors the structural-typing contract.
        from agentforge.eval.supervisor_adapter import (
            DocumentFixtureResolver,
        )

        class _Mini:
            def resolve(
                self, case: EvalCase
            ) -> tuple[list[RenderedPage], int | None]:
                return ([], None)

        instance: DocumentFixtureResolver = _Mini()
        pages, doc_id = instance.resolve(_evidence_case())
        assert pages == []
        assert doc_id is None
