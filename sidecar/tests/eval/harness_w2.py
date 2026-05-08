"""W2 eval harness: programmatic checks then LLM judge (Task 17.4).

Two-stage evaluation per case:

  1. **Programmatic checks** (cheap, deterministic) run first via
     :class:`ProgrammaticChecks`. Schema validation, citation
     presence, and PHI sweep all run on every case.
  2. **LLM judge** runs only when the case's category maps to a judge
     category (currently HALLUCINATION → factually_consistent and
     REFUSAL → safe_refusal). Cases without a judge mapping are
     programmatic-only — they pass when the cheap layer passes.

If the programmatic stage fails, the harness short-circuits without
calling the judge — judge spend is reserved for cases that already
clear schema / citation / PHI hygiene. Either layer failing fails the
case overall.

The W1 :class:`EvalHarness` (in :mod:`tests.eval.harness`) keeps its
existing contract — its ``evaluate()`` method is what the 1147 W1
tests consume. This module is the W2 superset; co-existence keeps the
W1 baseline reproducible while we calibrate the W2 layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from agentforge.observability.protocols import TraceHandle
from agentforge.schemas.citation import Citation

from tests.eval.graders.llm_judge_w2 import (
    JudgeCategory,
    LLMJudge,
    LLMJudgeOutcome,
)
from tests.eval.graders.programmatic import ProgrammaticChecks
from tests.eval.harness import EvalCase, EvalCategory


# Maps the EvalCategory the case author tagged onto the JudgeCategory
# the LLM judge runs. Categories absent from this dict are
# programmatic-only — adding a judge for them is a coordinated change
# (new prompt, new version.json entry, calibration).
_JUDGE_BY_CATEGORY: dict[EvalCategory, JudgeCategory] = {
    EvalCategory.HALLUCINATION: JudgeCategory.FACTUALLY_CONSISTENT,
    EvalCategory.REFUSAL: JudgeCategory.SAFE_REFUSAL,
}


@dataclass(frozen=True)
class W2EvalResult:
    """Outcome of one W2 case evaluation.

    ``judge_outcome`` is ``None`` for programmatic-only cases and for
    cases where the programmatic stage short-circuited the run.
    """

    case_id: str
    programmatic: ProgrammaticChecks
    judge_outcome: LLMJudgeOutcome | None

    @property
    def passed(self) -> bool:
        if not self.programmatic.passed:
            return False
        if self.judge_outcome is None:
            return True
        return self.judge_outcome.passed


class EvalHarnessW2:
    """Two-stage evaluator: programmatic first, then LLM judge."""

    def __init__(self, *, judge: LLMJudge, trace: TraceHandle) -> None:
        self._judge = judge
        self._trace = trace

    async def evaluate(
        self,
        *,
        case: EvalCase,
        response: str,
        structured_citation_payload: dict[str, Any],
        structured_citations: Sequence[Citation] = (),
        sources: str = "",
        logs: Iterable[str] = (),
    ) -> W2EvalResult:
        """Run programmatic checks, then conditionally run the LLM judge.

        ``sources`` is the stringified source-document context the
        agent had — only consumed by the LLM judge for factual
        consistency. ``logs`` is the trace-export lines for the PHI
        sweep.

        Iterables are materialised once so we don't re-consume an
        exhausted generator if a future caller passes one.
        """
        log_list = list(logs)
        programmatic = ProgrammaticChecks.run(
            response=response,
            structured_citation_payload=structured_citation_payload,
            structured_citations=structured_citations,
            logs=log_list,
        )

        if not programmatic.passed:
            # Short-circuit: cheap layer caught a regression; no point
            # spending judge tokens on a case that already failed.
            return W2EvalResult(
                case_id=case.id, programmatic=programmatic, judge_outcome=None
            )

        judge_category = _JUDGE_BY_CATEGORY.get(case.category)
        if judge_category is None:
            # Programmatic-only category — the cheap layer is the
            # whole verdict.
            return W2EvalResult(
                case_id=case.id, programmatic=programmatic, judge_outcome=None
            )

        outcome = await self._judge.grade(
            judge_category,
            response_text=response,
            sources=sources,
            case=case,
            trace=self._trace,
        )
        return W2EvalResult(
            case_id=case.id, programmatic=programmatic, judge_outcome=outcome
        )
