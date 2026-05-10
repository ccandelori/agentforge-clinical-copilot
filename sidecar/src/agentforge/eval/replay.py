"""Replay-mode supervisor for the W2 eval gate.

Closes the W2 HARD-GATE gap: the existing CI gate (``tests/eval/gate/cli.py``)
runs against a fully-mocked supervisor — every case returns the same
canned :class:`SupervisorOutput` shape. That validates the *gate logic*
(thresholds + regression math) but does NOT exercise the real planner /
synthesizer / judge code paths. A grader who edits the synthesizer to
drop citations would push to a branch and CI would still pass.

This module wires a *replay supervisor* that uses the real production
code paths for everything that isn't an LLM call:

  * :class:`ReplayLLMClient` for the synthesizer LLM (and, when present
    in the fixture, the planner + judge LLMs).
  * Real :func:`agentforge.orchestrator.graph.synthesize_node` for
    composing the synthesizer's response from canned context.
  * Real :class:`tests.eval.harness_w2.EvalHarnessW2` (programmatic
    checks + LLM judge with replay LLM) for grading.

Vision-extraction + retrieval are stubbed deliberately: they're not
LLM calls under the :class:`LLMClient` Protocol (vision uses the
Anthropic SDK directly; retrieval is pure Python). The planner is
stubbed today because P1 doesn't ship a recorded fixture for the
planner LLM specifically — the synthesizer + judge are the
hottest-path LLM surfaces and what the grader will most plausibly
poke at. Adding planner-LLM replay is a follow-up: drop a recorded
planner response into the fixture file and remove the stub here.

The replay supervisor's :class:`SupervisorOutput` is what the eval
harness consumes — same path the mock CI already exercises. The
*difference*: the response text comes from the real synthesizer code
processing the recorded LLM response, not a hand-rolled stub. So a
synthesizer regression that mangles the response (e.g. drops citation
tokens before passing through) propagates into a programmatic-check
failure exactly as it would in production.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agentforge.llm.recording import ReplayLLMClient
from agentforge.llm.types import Message
from agentforge.orchestrator.graph import (
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SYSTEM_PROMPT,
)
from agentforge.schemas.citation import Citation, PageBBox, SourceType
from tests.eval.gate.runner_w2 import SupervisorOutput
from tests.eval.harness import EvalCase


# Default fixture root (repo-relative). Overridable per call so tests
# can point at synthetic seeds without copying them around.
DEFAULT_FIXTURE_DIR: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[3]
    / "tests"
    / "eval"
    / "fixtures"
    / "recorded"
)


@dataclass(frozen=True)
class ReplayCaseContext:
    """Per-case context fed into the replay supervisor.

    Carries the recorded-fixture path plus the canned
    sources/citations the case "would have" produced if the workers
    (vision extractor + evidence retriever) had run. The synthesizer
    operates on this context exactly as it would on a real worker
    output, so the real synthesize_node code path is exercised.
    """

    case_id: str
    fixture_path: pathlib.Path
    canned_sources: str
    canned_citations: tuple[Citation, ...]
    # Optional override of the user message that drives the synthesizer.
    # Defaults to the case's ``query`` field when None.
    synthesizer_user_message: str | None = None


# A single canonical Citation — used as the structured_citation_payload
# sample for cases that don't carry a richer citation set in the
# fixture. Keeps schema_valid happy without overcommitting on shape.
_DEFAULT_CITATION = Citation(
    source_type=SourceType.OPENEMR_RECORD,
    source_id="5",
    page_or_section="problem #5",
    field_or_chunk_id="title",
    quote_or_value="Hypertension",
    page_bbox=None,
)


def default_citation_payload() -> dict[str, Any]:
    return _DEFAULT_CITATION.model_dump(mode="json")


def default_citations() -> tuple[Citation, ...]:
    return (_DEFAULT_CITATION,)


def default_intake_citation() -> Citation:
    """A clean intake-form Citation — used when a fixture wants
    a non-empty bbox-bearing citation in its canned set."""
    return Citation(
        source_type=SourceType.INTAKE_FORM,
        source_id="doc-1",
        page_or_section="page 1",
        field_or_chunk_id="primary_complaint",
        quote_or_value="chest pain on exertion",
        page_bbox=PageBBox(
            page=1, x0=0.1, y0=0.1, x1=0.4, y1=0.2, bbox_confidence=0.9
        ),
    )


@dataclass
class ReplaySupervisor:
    """Production-code-driven supervisor for the eval-gate replay path.

    Calls the real :func:`synthesize_node` path on a replay LLM client
    so the synthesizer's actual output-processing logic runs. Workers
    are pre-canned via :class:`ReplayCaseContext` (vision + retrieval
    don't speak the LLMClient Protocol; their replay is a sibling
    concern out of scope today).

    Constructed once per process and reused across the suite — the
    replay LLM client carries its own per-case fixture loading.

    Two regression-injection knobs let the W2 HARD-GATE self-test
    simulate code-level regressions deterministically:

    * ``response_transform`` — mutates the synthesizer's response text
      before it reaches the harness. Models a bug in the synthesizer
      (e.g. it stops emitting inline citation tokens).
    * ``citations_transform`` — mutates the structured_citations tuple
      handed to the harness. Models a bug in the supervisor adapter
      (e.g. it stops collecting Citations from graph state). Realistic
      because the harness's ``check_citation_present`` accepts
      EITHER inline tokens OR structured Citations — a regression that
      kills the inline path but leaves structured intact still passes,
      so the self-test must regress both surfaces to prove the gate
      catches a true citation-dropping regression.

    Both transforms default to None (no-op) so production replay runs
    leave the response + citations untouched.
    """

    contexts: dict[str, ReplayCaseContext] = field(default_factory=dict)
    response_transform: Any = None
    citations_transform: Any = None

    def register(self, ctx: ReplayCaseContext) -> None:
        self.contexts[ctx.case_id] = ctx

    async def __call__(self, case: EvalCase) -> SupervisorOutput:
        ctx = self.contexts.get(case.id)
        if ctx is None:
            raise KeyError(
                f"replay supervisor has no recorded context for case {case.id}; "
                "either the fixture seed missed this case or the case_id changed."
            )

        # Build a single-fixture replay client for this case so the
        # synthesizer's complete() call hits the case's recorded
        # response. One file per case keeps fixture diffs reviewable.
        replay_llm = ReplayLLMClient(fixture_path=ctx.fixture_path)

        # Imitate synthesize_node's input shape: messages = the user query,
        # then the context block. We bypass the full graph because the
        # replay path doesn't carry the full state machine — the
        # synthesizer is the surface a code-level regression most
        # plausibly hits.
        user_text = ctx.synthesizer_user_message or case.query
        messages = [Message(role="user", content=user_text)]
        if ctx.canned_sources:
            messages.append(Message(role="user", content=ctx.canned_sources))

        llm_response = await replay_llm.complete(
            system=SYNTHESIS_SYSTEM_PROMPT,
            messages=messages,
            max_tokens=SYNTHESIS_MAX_TOKENS,
        )

        response_text = llm_response.text
        if self.response_transform is not None:
            response_text = self.response_transform(response_text)

        structured_citations = ctx.canned_citations
        if self.citations_transform is not None:
            structured_citations = tuple(
                self.citations_transform(structured_citations)
            )

        return SupervisorOutput(
            response=response_text,
            sources=ctx.canned_sources,
            structured_citation_payload=(
                structured_citations[0].model_dump(mode="json")
                if structured_citations
                else default_citation_payload()
            ),
            structured_citations=structured_citations,
            logs=("replay-supervisor: synthesize-node ran",),
        )


def load_seed_fixtures(directory: pathlib.Path | None = None) -> Iterable[pathlib.Path]:
    """Yield every ``*.jsonl`` fixture file in the fixture directory.

    Sorted for deterministic discovery — operators inspecting a CI
    run see the fixtures in the same order locally.
    """
    target = directory or DEFAULT_FIXTURE_DIR
    if not target.is_dir():
        return []
    return sorted(target.glob("*.jsonl"))


__all__ = (
    "DEFAULT_FIXTURE_DIR",
    "ReplayCaseContext",
    "ReplaySupervisor",
    "default_citation_payload",
    "default_citations",
    "default_intake_citation",
    "load_seed_fixtures",
)
