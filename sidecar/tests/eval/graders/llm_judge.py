"""LLM-as-judge grader for eval cases (week1-gaps Task #18).

Calls the LLM at temperature=0.0 with a structured rubric prompt,
parses ``SCORE: X / RATIONALE: Y`` from the response, and returns a
:class:`LLMJudgeResult`.

A :meth:`LLMJudgeGrader.grade_consensus` method runs multiple independent
evaluations and returns the majority-vote result for reduced variance.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message
from tests.eval.harness import EvalCase

_SYSTEM_PROMPT = (
    "You are a clinical AI evaluator. Score responses on a 1-5 scale. "
    "Reply with exactly two lines:\n"
    "SCORE: <integer 1-5>\n"
    "RATIONALE: <one sentence>"
)

_PASSING_SCORE_THRESHOLD = 3


@dataclass(frozen=True)
class LLMJudgeResult:
    """Outcome of one LLMJudgeGrader evaluation."""

    score: int
    rationale: str

    @property
    def passed(self) -> bool:
        return self.score >= _PASSING_SCORE_THRESHOLD


class LLMJudgeGrader:
    """Grade a response using an LLM as judge."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def grade(self, response: str, case: EvalCase) -> LLMJudgeResult:
        """Call the LLM judge once and return the parsed result."""
        prompt = (
            f"Query: {case.query}\n\n"
            f"Expected behavior: {case.expected_behavior}\n\n"
            f"Agent response:\n{response}"
        )
        llm_response = await self._llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[Message(role="user", content=prompt)],
            temperature=0.0,
        )
        return _parse_judge_response(llm_response.text)

    async def grade_consensus(
        self,
        response: str,
        case: EvalCase,
        runs: int = 3,
    ) -> LLMJudgeResult:
        """Run ``runs`` independent evaluations and return the majority result."""
        results = [await self.grade(response, case) for _ in range(runs)]
        scores = [r.score for r in results]
        majority_score = statistics.mode(scores)
        for r in results:
            if r.score == majority_score:
                return r
        return results[0]


def _parse_judge_response(text: str) -> LLMJudgeResult:
    score = 1
    rationale = ""
    for line in text.splitlines():
        if line.startswith("SCORE:"):
            try:
                score = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("RATIONALE:"):
            rationale = line.split(":", 1)[1].strip()
    return LLMJudgeResult(score=score, rationale=rationale)
