"""W2 eval-gate runner (Tasks 18.2 + 18.3).

Loads the 50 W2 cases from ``tests/eval/cases/week2/``, dispatches each
through a caller-supplied ``supervisor`` callable (the LangGraph
supervisor in real runs; a mock in CI), grades the response with
:class:`EvalHarnessW2`, and aggregates per-category pass rates.

The runner is deliberately **not** the gate — it produces the
per-category pass-rate JSON the gate logic in :mod:`tests.eval.gate.gate`
(Task 18.4) consumes. Splitting the two means the runner is reusable
for ad-hoc eval runs (baseline regen, manual sweeps) where the gate's
non-zero-exit semantics would get in the way.

Adapter shape:
    The harness expects ``response``, ``sources``, structured
    citation fields, and trace logs. The W2 case YAML doesn't carry
    those (only ``id`` / ``category`` / ``patient_id`` / ``query`` /
    ``expected_behavior``) — the supervisor invocation is what produces
    the output. Tests pass a callable that maps an :class:`EvalCase` to
    a :class:`SupervisorOutput`; that callable is the seam where mocks
    or the real graph plug in.
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from agentforge.schemas.citation import Citation

from tests.eval.harness import EvalCase
from tests.eval.harness_w2 import EvalHarnessW2, W2EvalResult
from tests.eval.yaml_cases import load_yaml_cases


_DEFAULT_WEEK2_DIR: Final[pathlib.Path] = (
    pathlib.Path(__file__).resolve().parents[2]
    / "eval"
    / "cases"
    / "week2"
)


@dataclass(frozen=True)
class SupervisorOutput:
    """The harness-shaped output of one supervisor-graph invocation.

    The supervisor produces a free-text response plus the structured
    bookkeeping the W2 programmatic + judge layers need. Mocks fabricate
    these directly; the production adapter (out of scope for Task 18)
    will derive them from the LangGraph result.
    """

    response: str
    sources: str = ""
    structured_citation_payload: dict[str, Any] = field(default_factory=dict)
    structured_citations: tuple[Citation, ...] = ()
    logs: tuple[str, ...] = ()


@dataclass(frozen=True)
class W2RunnerResult:
    """One case's runner output.

    Pairs the input case with the harness verdict so the per-category
    aggregator can group by ``case.category`` without re-loading the
    case list.
    """

    case: EvalCase
    eval_result: W2EvalResult


# A supervisor is anything callable with an EvalCase that returns
# (sync or async) a SupervisorOutput. CI mocks return sync; real
# LangGraph adapters will be async.
SyncSupervisor = Callable[[EvalCase], SupervisorOutput]
AsyncSupervisor = Callable[[EvalCase], Awaitable[SupervisorOutput]]
Supervisor = SyncSupervisor | AsyncSupervisor


def load_week2_cases(
    directory: pathlib.Path | None = None,
) -> list[EvalCase]:
    """Load every YAML case in the W2 cases directory.

    Returns a flattened list across all five W2 yaml files in
    deterministic (filename-sorted) order. Empty directory returns
    an empty list — callers that want the suite-size guarantee should
    assert ``len(cases) == 50`` themselves (the gate runner does).
    """
    target = directory or _DEFAULT_WEEK2_DIR
    if not target.is_dir():
        return []
    cases: list[EvalCase] = []
    for yaml_path in sorted(target.glob("*.yaml")):
        cases.extend(load_yaml_cases(yaml_path))
    return cases


async def _invoke_supervisor(
    supervisor: Supervisor, case: EvalCase
) -> SupervisorOutput:
    """Call ``supervisor`` and await its result if it is a coroutine.

    Lets test mocks be ``def`` while real adapters are ``async def`` —
    the runner stays single-pathed.
    """
    output = supervisor(case)
    if inspect.isawaitable(output):
        return await output
    return output


async def run_week2_suite(
    *,
    cases: Sequence[EvalCase],
    supervisor: Supervisor,
    harness: EvalHarnessW2,
) -> list[W2RunnerResult]:
    """Run every case through ``supervisor`` and grade with ``harness``.

    Cases run sequentially. The W1 :class:`EvalRunner` enforces the
    same discipline (orchestrator state has per-turn ContextVars that
    don't tolerate concurrent asyncio tasks) — keeping the W2 runner
    sequential preserves the same property and avoids leaking state
    across cases.
    """
    results: list[W2RunnerResult] = []
    for case in cases:
        output = await _invoke_supervisor(supervisor, case)
        eval_result = await harness.evaluate(
            case=case,
            response=output.response,
            structured_citation_payload=output.structured_citation_payload,
            structured_citations=output.structured_citations,
            sources=output.sources,
            logs=output.logs,
        )
        results.append(W2RunnerResult(case=case, eval_result=eval_result))
    return results


__all__ = (
    "SupervisorOutput",
    "W2RunnerResult",
    "load_week2_cases",
    "run_week2_suite",
)


# Keep a no-op reference to ``asyncio`` so static analysers don't strip
# it; the import is reserved for callers who want to schedule
# ``run_week2_suite`` from a sync entry point with ``asyncio.run``.
_ = asyncio
