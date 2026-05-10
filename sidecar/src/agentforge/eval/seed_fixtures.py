"""Build / refresh the synthetic fixture seed for the eval-replay CI gate.

The recorded-fixtures CI gate (``agentforge.eval.replay``) needs a JSONL
fixture per case so the replay LLM client has a recorded response to
serve. Two ways to generate the seed:

1. **Real-recorded fixtures.** A human runs the regen CLI in
   ``--record`` mode (~$1.54 of Anthropic spend) and the
   :class:`RecordingLLMClient` writes the canonical request + response
   for every LLM call in the suite. Authoritative; lands once a
   meaningful agent change ships.

2. **Synthetic seed (this module).** A Python module that crafts
   canned :class:`LLMResponse` objects and computes the *exact* request
   hash the synthesizer's `complete()` call will emit, then writes the
   hash + response pair as a fixture file.

Why the synthetic seed exists alongside the real recorder:

* CI must catch a synthesizer regression on every push, today.
* The first real-recorded fixture run is reserved for after the W2
  cleanup polish ships (cost contract + lab-extractor wiring).
* A synthetic seed exercises the same code path the real recording
  would; the only difference is the LLM response itself is a
  hand-rolled stub, not Anthropic-generated text. The
  programmatic-checks layer + the gate logic don't care about the
  text content — they care about the citations + claim shape.

This module is the source of truth for the fixture contents. Re-run
::

    cd sidecar
    uv run python -m agentforge.eval.seed_fixtures \\
        --output tests/eval/fixtures/recorded

after editing any of the synthesizer's ``complete()`` call shape (e.g.
the ``SYNTHESIS_SYSTEM_PROMPT`` text, the ``SYNTHESIS_MAX_TOKENS``
constant, or the message construction in :mod:`agentforge.eval.replay`).
The fixtures are committed so CI can run without network.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from agentforge.llm.recording import RecordedCall, _canonical_request, hash_request
from agentforge.llm.types import LLMResponse, Message
from agentforge.orchestrator.graph import (
    SYNTHESIS_MAX_TOKENS,
    SYNTHESIS_SYSTEM_PROMPT,
)


@dataclass(frozen=True)
class SyntheticFixture:
    """One synthetic fixture entry — case_id + canned response.

    The fixture's request key is computed at write time from the
    case's synthesizer input shape (system prompt + messages + max
    tokens). The user message is pulled from the case YAML at write
    time so seed and replay are always in sync — refactoring the case
    query forces a fixture refresh on the next CI run, which is the
    right failure mode.

    ``canned_sources`` is the fixture's "what the workers would have
    produced" stub. The replay supervisor passes it to the synthesizer
    as a second user message, mirroring what the real graph injects
    via ``_build_synthesis_context_block``. Empty string means the
    synthesizer sees only the user query.
    """

    case_id: str
    canned_sources: str
    response_text: str
    # Token counts on the canned response are arbitrary — the harness
    # doesn't inspect them. They're set to plausible-looking integers
    # so the fixture files look like real recordings on inspection.
    input_tokens: int = 1500
    output_tokens: int = 250


# ---------------------------------------------------------------------------
# Synthetic seed corpus
# ---------------------------------------------------------------------------
# Every entry below is one fixture. The set deliberately covers all
# five W2 categories so the replay path exercises programmatic-only
# cases (extraction, evidence_retrieval, citations, missing_data) and
# judge-routed cases (refusal). Response texts include inline citation
# tokens so the W1 grammar check (find_citations) trips PASS — a
# code-level regression that drops the citations from the response
# (or the citation extractor that parses them) makes the
# programmatic check fail and the gate trips.
#
# Case IDs match the real W2 case YAML so a future real-recorded fixture
# replaces these without renaming. The user message is pulled from the
# case YAML at write time (see _load_case_query) — do NOT hard-code
# the query text here, or the seed will drift from the case file the
# next time someone edits a yaml entry.

SEED_FIXTURES: tuple[SyntheticFixture, ...] = (
    # ---- citations ---------------------------------------------------
    SyntheticFixture(
        case_id="w2_cit_01",
        canned_sources=(
            "EXTRACTION:\n"
            '{"chief_concern": "Hypertension"}\n\n'
            "EVIDENCE:\n[guideline #abc] (lipids-aha-acc-2018) Statin "
            "benefit groups include diabetes mellitus."
        ),
        response_text=(
            "Patient has active hypertension [problem #5]. Current medication "
            "lisinopril 10mg daily [med #12]. No documented allergies "
            "[allergy #0]. Last A1c 7.2 [observation #44]."
        ),
    ),
    SyntheticFixture(
        case_id="w2_cit_06",
        canned_sources=(
            "EVIDENCE:\n[guideline #ada-a1c-001] (diabetes-ada-standards) "
            "A1c goal for most non-pregnant adults is below 7.0%."
        ),
        response_text=(
            "Per the ADA Standards of Care, the A1c goal for most non-pregnant "
            "adults is below 7.0% [guideline #ada-a1c-001]. For this patient, "
            "individualized considerations apply [problem #5]."
        ),
    ),
    # ---- evidence_retrieval -----------------------------------------
    SyntheticFixture(
        case_id="w2_evr_01",
        canned_sources=(
            "EVIDENCE:\n[guideline #ada-a1c-001] (diabetes-ada-standards) "
            "A1c goal for most non-pregnant adults is below 7.0%; individualize "
            "based on age, comorbidities, and life expectancy."
        ),
        response_text=(
            "The ADA recommends an A1c target below 7.0% for most non-pregnant "
            "adults with diabetes [guideline #ada-a1c-001], with "
            "individualization for age, comorbidities, and hypoglycemia risk."
        ),
    ),
    # ---- extraction --------------------------------------------------
    SyntheticFixture(
        case_id="w2_ext_01",
        canned_sources=(
            "EXTRACTION:\n"
            '{"chief_complaint": "chest pain on exertion", "medications": '
            '["lisinopril 10mg"], "allergies": []}'
        ),
        response_text=(
            "Extracted intake summary: chief complaint chest pain on exertion "
            "[intake #1], current medication lisinopril 10mg [med #2], no "
            "documented allergies [allergy #0]."
        ),
    ),
    # ---- missing_data ------------------------------------------------
    SyntheticFixture(
        case_id="w2_md_01",
        canned_sources=(
            "EXTRACTION:\n"
            '{"family_history": null, "unsupported_fields": '
            '["family_history: confidence below 0.7 floor"]}'
        ),
        response_text=(
            "The family-history field on this intake form is not legible at "
            "sufficient confidence to extract [intake #1]. The field landed in "
            "the unsupported_fields bucket under the 0.7 confidence floor; "
            "recommend a fresh upload or manual chart review."
        ),
    ),
    # ---- refusal -----------------------------------------------------
    # Note: refusal cases route to the LLM judge — the judge-LLM call
    # is mocked at the harness level (the gate's mock-judge AsyncMock
    # always returns PASS). We do NOT add a judge-replay fixture here;
    # the synthesizer LLM call is the one that goes through replay.
    SyntheticFixture(
        case_id="w2_ref_01",
        canned_sources="",
        response_text=(
            "I cannot fulfil that request without provider review [policy #1]. "
            "Increasing metformin and ordering a lipid panel are clinical "
            "actions that require an authorized provider's signature; please "
            "escalate to the patient's PCP."
        ),
    ),
)


def _load_case_query(case_id: str) -> str:
    """Look up the case's query from the YAML cases at fixture-write time.

    Importing the loader inline keeps this module light at import time —
    the YAML loader walks the cases dir; doing it inside the function
    means `python -c 'import agentforge.eval.seed_fixtures'` doesn't
    pay for that walk.
    """
    from tests.eval.gate.runner_w2 import load_week2_cases

    for case in load_week2_cases():
        if case.id == case_id:
            return case.query
    raise KeyError(
        f"seed fixture references case_id={case_id!r} but no W2 case "
        "has that id; either the case yaml moved or the fixture is stale."
    )


def _build_record(fixture: SyntheticFixture) -> RecordedCall:
    """Construct a :class:`RecordedCall` whose hash matches the live request.

    The hash *must* match what :class:`ReplaySupervisor` computes when
    it calls ``ReplayLLMClient.complete``. We mirror that input shape
    exactly: same system prompt, same message construction, same max
    tokens. If the replay supervisor's input shape ever drifts from
    this, regenerate the fixtures via the CLI below.
    """
    user_message = _load_case_query(fixture.case_id)
    messages = [Message(role="user", content=user_message)]
    if fixture.canned_sources:
        messages.append(Message(role="user", content=fixture.canned_sources))

    request = _canonical_request(
        system=SYNTHESIS_SYSTEM_PROMPT,
        messages=messages,
        tools=None,
        max_tokens=SYNTHESIS_MAX_TOKENS,
        temperature=1.0,  # synthesize_node doesn't override the default
    )
    digest = hash_request(request)
    response = LLMResponse(
        text=fixture.response_text,
        tool_calls=[],
        stop_reason="end_turn",
        input_tokens=fixture.input_tokens,
        output_tokens=fixture.output_tokens,
    )
    return RecordedCall(
        request_hash=digest,
        request=request,
        response=response,
        label=f"case_id={fixture.case_id},node=synthesizer,source=synthetic-seed",
    )


def write_seed_fixtures(output_dir: pathlib.Path) -> int:
    """Write one ``<case_id>.jsonl`` per synthetic fixture entry.

    Returns the number of fixture files written. Idempotent — re-runs
    overwrite existing files with the same content (modulo any change
    to :data:`SEED_FIXTURES` or the synthesizer's call shape).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for fixture in SEED_FIXTURES:
        record = _build_record(fixture)
        path = output_dir / f"{fixture.case_id}.jsonl"
        path.write_text(record.to_jsonl() + "\n", encoding="utf-8")
        written += 1
    return written


def write_index(output_dir: pathlib.Path) -> pathlib.Path:
    """Write a tiny ``index.json`` listing every fixture + its case_id.

    The replay supervisor doesn't need the index — it loads each
    fixture via :class:`ReplayCaseContext`. The index is for human
    inspection / CI debugging: a developer can ``cat`` it to see which
    cases the seed covers without having to grep across JSONL files.
    """
    index_path = output_dir / "index.json"
    payload = {
        "fixture_kind": "synthetic-seed",
        "synthesizer_max_tokens": SYNTHESIS_MAX_TOKENS,
        "cases": [
            {
                "case_id": fx.case_id,
                "fixture_file": f"{fx.case_id}.jsonl",
                "user_message_excerpt": _load_case_query(fx.case_id)[:80],
            }
            for fx in SEED_FIXTURES
        ],
    }
    index_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return index_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentforge.eval.seed_fixtures",
        description=(
            "Write the synthetic fixture seed for the eval-replay CI gate. "
            "Run after editing the synthesizer's call shape so the recorded "
            "request hashes line up with what the replay supervisor will emit."
        ),
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        required=True,
        help="Directory to write the seed fixtures into.",
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    written = write_seed_fixtures(args.output)
    index_path = write_index(args.output)
    print(
        f"wrote {written} fixture file(s) + index.json under {args.output}",
        file=sys.stderr,
    )
    print(str(index_path))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(main())


__all__ = (
    "SEED_FIXTURES",
    "SyntheticFixture",
    "main",
    "write_index",
    "write_seed_fixtures",
)
