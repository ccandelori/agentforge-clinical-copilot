"""Binary LLM-as-judge for the W2 eval suite (Task 17).

Two judge categories. Each loads its own prompt from the versioned
prompt library at ``prompts/v1/judge_<category>.md`` and emits a
binary PASS/FAIL verdict on a single agent response.

  * :class:`JudgeCategory.FACTUALLY_CONSISTENT` — does every claim in
    the response trace back to the source documents the agent had?
  * :class:`JudgeCategory.SAFE_REFUSAL` — for refusal cases, did the
    agent decline an unsafe or out-of-scope request?

The W1 ``LLMJudgeGrader`` (1-5 score, consensus voting) lives next door
in :mod:`tests.eval.graders.llm_judge` and stays unchanged — its
clinical-relevance rubric is orthogonal to the binary contracts here.

Determinism is the design constraint: the judge call goes out at
``temperature=0`` and parses a structured ``VERDICT: PASS|FAIL`` token
out of the response. Free-form responses fall back to FAIL so a
malformed judge can never silently bless a bad agent response.

Observability:
The judge is a real LLM call, so it gets the same Langfuse treatment
as orchestrator and extraction calls — :meth:`LangfuseClient.record_llm_call`
with computed ``cost_usd`` from the existing pricing table. No new
tracking surface invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message
from agentforge.observability.cost import calculate_cost
from agentforge.observability.protocols import LangfuseClient, TraceHandle
from agentforge.prompts import load_prompt

from tests.eval.harness import EvalCase


class JudgeCategory(StrEnum):
    """Closed set of W2 judge categories.

    Each value maps 1:1 to a prompt-library component name
    (``judge_<value>``). Adding a new category is a coordinated change:
    new prompt file, new ``version.json`` entry, new harness wiring.
    """

    FACTUALLY_CONSISTENT = "factually_consistent"
    SAFE_REFUSAL = "safe_refusal"


class JudgeVerdict(StrEnum):
    """Binary verdict the judge LLM emits."""

    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class LLMJudgeOutcome:
    """One judge call's parsed result.

    ``attempts`` and ``tiebreaker_used`` describe how the verdict was
    reached. Single-call ``grade()`` returns ``attempts=1,
    tiebreaker_used=False``; ``grade_with_retry()`` may bump
    ``attempts`` to 2 (agreement on first retry) or 3 (tiebreaker ran)
    and flips ``tiebreaker_used`` accordingly. Surfacing these in the
    outcome lets eval reports flag cases where the judge oscillated —
    a signal the case wording or judge prompt needs tightening.
    """

    category: JudgeCategory
    verdict: JudgeVerdict
    rationale: str
    attempts: int = 1
    tiebreaker_used: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict is JudgeVerdict.PASS


_PROMPT_COMPONENTS: dict[JudgeCategory, str] = {
    JudgeCategory.FACTUALLY_CONSISTENT: "judge_factually_consistent",
    JudgeCategory.SAFE_REFUSAL: "judge_safe_refusal",
}


class LLMJudge:
    """Run binary PASS/FAIL judge calls and record cost through Langfuse."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        langfuse: LangfuseClient,
        model: str,
    ) -> None:
        self._llm = llm
        self._langfuse = langfuse
        self._model = model

    async def grade(
        self,
        category: JudgeCategory,
        *,
        response_text: str,
        sources: str,
        case: EvalCase,
        trace: TraceHandle,
    ) -> LLMJudgeOutcome:
        """Issue one judge call and return the parsed outcome.

        ``sources`` is the stringified source-document context the agent
        had access to — for factual consistency it carries tool result
        bodies / extracted document text; for refusal cases it can be
        the empty string (the judge needs only the response).
        """
        verdict, rationale = await self._call_once(
            category=category,
            response_text=response_text,
            sources=sources,
            case=case,
            trace=trace,
        )
        return LLMJudgeOutcome(
            category=category, verdict=verdict, rationale=rationale
        )

    async def grade_with_retry(
        self,
        category: JudgeCategory,
        *,
        response_text: str,
        sources: str,
        case: EvalCase,
        trace: TraceHandle,
    ) -> LLMJudgeOutcome:
        """Run two judge calls; on disagreement, run a tiebreaker.

        At ``temperature=0`` two calls on identical inputs *should*
        match. They occasionally don't — the model is genuinely on the
        boundary, the prompt has an ambiguity, or the response itself
        is borderline. In those rare cases we run one tiebreaker and
        majority-vote the three.

        Cost trade-off: every case pays for a second call. The
        amortised cost is ~2x the single-call path; the tiebreaker
        only fires on the rare disagreement, so total spend stays at
        ~2.x in the common case. The signal is worth it — without
        retries the eval suite reports false-positive failures any
        time the judge wobbles.
        """
        first_verdict, first_rationale = await self._call_once(
            category=category,
            response_text=response_text,
            sources=sources,
            case=case,
            trace=trace,
        )
        second_verdict, second_rationale = await self._call_once(
            category=category,
            response_text=response_text,
            sources=sources,
            case=case,
            trace=trace,
        )

        if first_verdict is second_verdict:
            return LLMJudgeOutcome(
                category=category,
                verdict=first_verdict,
                rationale=first_rationale,
                attempts=2,
                tiebreaker_used=False,
            )

        third_verdict, third_rationale = await self._call_once(
            category=category,
            response_text=response_text,
            sources=sources,
            case=case,
            trace=trace,
        )
        # Tally: tiebreaker breaks the tie. With first/second split,
        # whichever side third matches wins — equivalent to majority
        # over all three.
        passes = sum(
            1
            for v in (first_verdict, second_verdict, third_verdict)
            if v is JudgeVerdict.PASS
        )
        majority = JudgeVerdict.PASS if passes >= 2 else JudgeVerdict.FAIL
        # Pick the rationale from a call that voted with the majority
        # so the report explanation matches the verdict.
        rationales = (first_rationale, second_rationale, third_rationale)
        verdicts = (first_verdict, second_verdict, third_verdict)
        chosen_rationale = next(
            (r for v, r in zip(verdicts, rationales, strict=True) if v is majority),
            "",
        )
        return LLMJudgeOutcome(
            category=category,
            verdict=majority,
            rationale=chosen_rationale,
            attempts=3,
            tiebreaker_used=True,
        )

    async def _call_once(
        self,
        *,
        category: JudgeCategory,
        response_text: str,
        sources: str,
        case: EvalCase,
        trace: TraceHandle,
    ) -> tuple[JudgeVerdict, str]:
        """Issue one judge call and return (verdict, rationale).

        Centralises prompt loading, LLM dispatch, observability, and
        parsing so ``grade()`` and ``grade_with_retry()`` agree on the
        per-call shape.
        """
        system_prompt = load_prompt(_PROMPT_COMPONENTS[category])
        user_prompt = _build_user_prompt(
            category=category, case=case, response_text=response_text, sources=sources
        )
        llm_response = await self._llm.complete(
            system=system_prompt,
            messages=[Message(role="user", content=user_prompt)],
            temperature=0.0,
        )
        cost = calculate_cost(
            self._model, llm_response.input_tokens, llm_response.output_tokens
        )
        self._langfuse.record_llm_call(
            trace,
            model=self._model,
            prompt_tokens=llm_response.input_tokens,
            completion_tokens=llm_response.output_tokens,
            latency_ms=0,
            cost_usd=cost,
        )
        return _parse_judge_text(llm_response.text)


def _build_user_prompt(
    *,
    category: JudgeCategory,
    case: EvalCase,
    response_text: str,
    sources: str,
) -> str:
    """Assemble the per-call user message the judge sees.

    Same skeleton for both categories — the system prompt routes
    behaviour. We pass the original case query and the test author's
    expected_behavior so the judge has the full task framing, not just
    the response in isolation.
    """
    sources_block = sources if sources else "(no source documents provided)"
    return (
        f"Eval category: {category.value}\n\n"
        f"Original query:\n{case.query}\n\n"
        f"Expected behavior:\n{case.expected_behavior}\n\n"
        f"Source documents the agent had access to:\n{sources_block}\n\n"
        f"Agent response:\n{response_text}"
    )


def _parse_judge_text(text: str) -> tuple[JudgeVerdict, str]:
    """Extract VERDICT and RATIONALE from the judge's reply.

    Strict parsing: an unparseable verdict is treated as FAIL. The
    rationale is best-effort — missing rationale yields the empty
    string, not a hard error, since the test author cares about
    pass/fail flow, not log polish.
    """
    verdict: JudgeVerdict | None = None
    rationale = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            value = stripped.split(":", 1)[1].strip().upper()
            if value == JudgeVerdict.PASS.value:
                verdict = JudgeVerdict.PASS
            elif value == JudgeVerdict.FAIL.value:
                verdict = JudgeVerdict.FAIL
        elif stripped.startswith("RATIONALE:"):
            rationale = stripped.split(":", 1)[1].strip()
    if verdict is None:
        # Defensive: an LLM that fails to follow the format gets
        # treated as FAIL so a misbehaving judge can't silently bless
        # a hallucinating agent.
        return JudgeVerdict.FAIL, rationale
    return verdict, rationale
