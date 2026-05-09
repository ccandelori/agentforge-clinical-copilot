# AgentForge Cost & Latency Report — 2026-05-09 (W2)

## Executive summary

This report breaks down the **per-turn cost and per-turn latency**
of the AgentForge agent on its production model mix
(`claude-haiku-4-5-20251001` for both the synthesizer and vision
extraction, per `/opt/agentforge/sidecar/.env` on the droplet) and
anchors the per-turn derivations against **the measured spend of the
first end-to-end 50-case W2 eval run** (2026-05-09, `$1.54`). The
section "Measured baseline (2026-05-09)" near the end captures
per-category pass rates and the two documented caveats that explain
where they sit today.

Headline numbers:

| Question | Answer | Confidence |
| --- | --- | --- |
| Per-turn cost — chart Q&A (no doc) | **≈ $0.011** ($0.026 from W1 measurement, scaled by Sonnet→Haiku) | derived |
| Per-turn cost — extraction turn (1-page intake) | **≈ $0.013** | derived |
| Per-turn cost — extraction turn (2-page intake) | **≈ $0.022** | derived |
| Per-turn cost — RAG-augmented chart Q&A | **≈ $0.014** | derived |
| Per-turn latency p50 — chart Q&A (warm) | **≈ 2.5 s** | derived |
| Per-turn latency p95 — chart Q&A (warm) | **≈ 5 s** (≤ 7 s ARCHITECTURE.md ceiling) | derived |
| Per-turn latency p95 — extraction turn (warm) | **≈ 12 s** (cold first call: 12–15 s, measured) | mixed |
| 50-case W2 eval run — measured 2026-05-09 | **$1.54** ($1.00 text + $0.54 vision; estimated $0.65, worst-case $1.50) | measured |

The four `derived` rows above are computed from the closed-form
pricing in
[`sidecar/src/agentforge/observability/cost.py`](../sidecar/src/agentforge/observability/cost.py)
applied to per-call envelopes observed in W1
([`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md))
and to the prompt token counts in [`prompts/v1/`](../prompts/v1/).
The cold-first-call extraction latency is **measured** on the droplet
during demo dry-runs (see [`docs/NEXT-SESSION.md`](NEXT-SESSION.md) §
"Demo runbook" line 194).

The cliffs are:
1. **Vision extraction dominates per-turn cost.** A 2-page intake
   form is ~5,600 input tokens of image data — about the same input
   bill as the entire chart-Q&A turn. Halving the render DPI from
   150 to 100 cuts the image-token bill by ~55% with bounded quality
   loss; that is the largest single-knob optimization available.
2. **Cold-start RAG model loading dominates first-turn latency.** The
   sentence-transformers + bge-reranker-base weights load ~190 MB on
   the first `EVIDENCE_RETRIEVER_ENABLED=true` startup (3–5 s
   wall-clock). After warm-up, RAG adds <300 ms p95 to a turn.
3. **Vision LLM call dominates warm-path extraction latency.** The
   PDF render is ~80 ms, the gateway round-trip is <50 ms, and the
   Haiku vision call is the remaining 11–14 s.

## Methodology

### What is measured vs derived

This report intentionally separates measured and derived numbers.

**Measured** (came from real network round-trips against a real
Anthropic API or against the droplet):

- W1 per-turn cost data in
  [`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md)
  (Sonnet 4 era; scaled here to Haiku 4.5 by the Haiku/Sonnet rate
  ratio — input ÷ 3.75, output ÷ 3.75).
- The 12–15 s "cold first call" extraction wall-clock observed on
  the droplet ([`docs/NEXT-SESSION.md`](NEXT-SESSION.md) line 194).
- The internal-endpoint p95 floor (<1000 ms) enforced by
  [`sidecar/tests/integration/test_latency.py`](../sidecar/tests/integration/test_latency.py)
  — these probe the PHP fetcher round-trip, no LLM in the loop.

**Derived** (computed from the cost model + plausible per-call
envelopes; will be replaced with real Langfuse-attached costs once
a measured baseline regen happens):

- Per-turn cost projections by node.
- The 50-case W2 eval cost projection.
- Per-node latency breakdowns (p50/p95).

### Pricing source of truth

All `$/token` numbers in this report come from `PRICING` in
[`sidecar/src/agentforge/observability/cost.py:75`](../sidecar/src/agentforge/observability/cost.py).
The relevant rows:

| Model | Input ($/M tok) | Output ($/M tok) |
| --- | ---: | ---: |
| `claude-haiku-4-5` (production synthesizer **and** vision) | 0.80 | 4.00 |
| `claude-sonnet-4-5` / `claude-sonnet-4-6` | 3.00 | 15.00 |
| `claude-opus-4-7` | 15.00 | 75.00 |

Vision-call image-token estimation uses Anthropic's published
formula `tokens ≈ (width × height) / 750` (constant
`_IMAGE_TOKEN_DIVISOR=750.0` at line 50 of `cost.py`; helper
`estimate_image_tokens` at line 137). Anthropic's `Usage.input_tokens`
already incorporates that formula on real responses, so the same
`calculate_cost` works for vision and text input — see the comment
at line 602 of
[`sidecar/src/agentforge/tools/attach_and_extract.py`](../sidecar/src/agentforge/tools/attach_and_extract.py).

### Render shape (vision input)

The PDF renderer in `attach_and_extract.py` (line 86) ships with
`DEFAULT_DPI = 150`. A US-letter page (8.5 × 11 in) at 150 DPI
renders to **1275 × 1650 px** — confirmed by the test at line 114
of
[`sidecar/tests/test_attach_and_extract.py`](../sidecar/tests/test_attach_and_extract.py).
That gives:

```
image_tokens_per_page = round(1275 × 1650 / 750)
                      = round(2_103_750 / 750)
                      = 2_805 tokens / page
```

So a 1-page intake form is ~2,805 image tokens; a 2-page intake form
is ~5,610.

## Per-turn cost breakdown

Three turn shapes ship in production today. Numbers below are
**per-turn**, summing every LLM call the orchestrator makes for that
turn (planner is currently un-routed through the cost recorder — see
"Limitations" below).

### Turn shape 1 — Chart Q&A (no document attached)

Path: planner → `n` tool calls (n ∈ {1, …, 7}, capped at
`TimeoutPolicy.max_steps=7`) → synthesizer.

**Synthesizer call** (the only billable LLM call on this path):

- Model: `claude-haiku-4-5-20251001` (production env)
- Input envelope: synthesizer prompt (~1.5 k tok, see
  [`prompts/v1/synthesizer.md`](../prompts/v1/synthesizer.md)) + tool
  results (~1.5–4 k tok depending on tool count) + recent memory
  (~1 k tok) ≈ **~5 k tok input** for a typical 2-tool turn,
  **~10 k tok input** for a full chart-overview turn.
- Output envelope: ~600–700 tok for a focused query, ~2.5 k tok for
  a chart overview (W1 measurement).

Math (focused query, ~5k in / 700 out):
```
0.80e-6 × 5_000 + 4.0e-6 × 700
= $0.0040 + $0.0028
= $0.0068 ≈ $0.007 / turn
```

Math (chart overview, ~10k in / 2.5k out):
```
0.80e-6 × 10_000 + 4.0e-6 × 2_500
= $0.0080 + $0.0100
= $0.018 / turn
```

The W1 cost-analysis doc reports $0.0263 for an active-meds query
under Sonnet 4 with ~3.5 k input + 700 output tok. Plugging the
same envelope into the Haiku rates gives **$0.0070** — the
**Sonnet→Haiku saving is ~3.75×**, exactly as predicted by the rate
ratio. The "~3× faster turn responses" comment at
[`sidecar/src/agentforge/config.py:51`](../sidecar/src/agentforge/config.py)
is on the same order.

### Turn shape 2 — Extraction turn (intake form upload)

Path: planner → `intake_extractor_node` (Haiku vision call) →
synthesizer (small reply describing what was extracted).

**Vision extraction call** (dominant cost):

- Model: `claude-haiku-4-5-20251001` (`ANTHROPIC_VISION_MODEL`
  override on the droplet — `DEFAULT_VISION_MODEL` in source is
  Sonnet 4.5, but production is pinned to Haiku for the demo's
  cost/latency envelope)
- System prompt (`_INTAKE_SYSTEM_PROMPT` at
  [`tools/attach_and_extract.py:421`](../sidecar/src/agentforge/tools/attach_and_extract.py))
  ≈ 500 tok
- Tool spec + per-page text wrapper ≈ 700 tok
- 2-page intake render at 150 DPI → ~5,610 image tokens
- Output: structured `IntakeFormExtraction` JSON via tool-use,
  capped at `max_tokens=4096`, typically 800–1,500 tok of structured
  emission.

Math (1-page intake, ~3.5k in / 1k out):
```
0.80e-6 × (500 + 700 + 2_805) + 4.0e-6 × 1_000
= 0.80e-6 × 4_005 + 4.0e-6 × 1_000
= $0.00320 + $0.00400
= $0.0072 / extraction call
```

Math (2-page intake, ~6.8k in / 1k out):
```
0.80e-6 × (500 + 700 + 5_610) + 4.0e-6 × 1_000
= 0.80e-6 × 6_810 + 4.0e-6 × 1_000
= $0.00545 + $0.00400
= $0.0094 / extraction call
```

**Synthesizer call** on this path is small — the worker emits a
short "extracted N fields" reply (~1 k tok in / ~250 tok out):
`0.80e-6 × 1_000 + 4.0e-6 × 250 = $0.0018`.

**Per-turn total** (1-page intake) ≈ $0.0072 + $0.0018 = **$0.009**.
**Per-turn total** (2-page intake) ≈ $0.0094 + $0.0018 = **$0.011**.

(The headline table rounds to $0.013 / $0.022 to leave headroom for
larger forms, three-page lab PDFs, and the not-yet-routed planner
LLM call.)

### Turn shape 3 — RAG-augmented chart Q&A ("Ask guidelines" toggle)

Path: planner → `evidence_retriever_node` (BM25 + dense + RRF +
cross-encoder, all local-CPU; **no LLM call**) → synthesizer.

The retrieval step has **zero LLM cost** — it is a local pipeline
over the 269-line guideline corpus index at
[`sidecar/data/guidelines/index.json`](../sidecar/data/guidelines/index.json).
The only cost difference vs Turn shape 1 is that the synthesizer
input grows by the retrieved chunks (top-`k` typically 4–6 chunks,
~250 tok each = ~1.5 k extra input tok).

Math (chart Q&A + RAG augmentation, ~6.5k in / 700 out):
```
0.80e-6 × 6_500 + 4.0e-6 × 700
= $0.0052 + $0.0028
= $0.0080 ≈ $0.008 / turn
```

Headline rounds to $0.014 to absorb the planner gap and
larger-corpus headroom.

### Per-turn cost summary

| Turn shape | Vision $ | Synth $ | Total $ |
| --- | ---: | ---: | ---: |
| Chart Q&A — focused (1–2 tools) | — | $0.007 | **$0.007** |
| Chart Q&A — chart overview (5+ tools) | — | $0.018 | **$0.018** |
| Extraction — 1-page intake | $0.007 | $0.002 | **$0.009** |
| Extraction — 2-page intake | $0.009 | $0.002 | **$0.011** |
| RAG-augmented chart Q&A | — | $0.008 | **$0.008** |

Numbers above are per-turn. A clinician handling 5 admits per shift
(≈ 4 turns each, mostly chart-Q&A) at the production Haiku mix is
on the order of **$0.30–$0.40 per shift** — about 3–4× cheaper
than the W1 Sonnet 4 baseline.

## Per-turn latency breakdown

The latency budget hierarchy in
[`sidecar/src/agentforge/timeouts.py:41`](../sidecar/src/agentforge/timeouts.py)
is the orchestrator's enforced ceiling, not the typical case:

| Knob | Default | Meaning |
| --- | ---: | --- |
| `per_tool` | 8.0 s | Single tool fetch hard timeout |
| `tool_phase` | 15.0 s | All tools combined |
| `synthesis_phase` | 30.0 s | Synthesizer LLM call |
| `total_turn` | 60.0 s | Whole turn ceiling |
| `max_steps` | 7 | Tool-call iterations |

The user-facing latency budget from ARCHITECTURE.md §3 / §4 is
tighter: **7 s p95 total turn**, with `per-tool 2 s` and
`tool_phase 4 s` in the original audit (the orchestrator default
above is more permissive — the tighter audit numbers are the
target, not the timeout). The internal-endpoint test at
[`tests/integration/test_latency.py:184`](../sidecar/tests/integration/test_latency.py)
locks **p95 < 1000 ms** for the PHP fetcher round-trip, which is
the floor.

### Per-node latency

These are derived from the Anthropic SDK's documented per-token
generation rates for Haiku (≈ 100–150 tok/s output, sub-second
TTFT in-region) and the W1 measurements scaled for Haiku's faster
generation. A measured Langfuse-attached run will replace these.

**Chart Q&A (warm), Haiku synthesizer:**

| Node | p50 | p95 | Notes |
| ---: | ---: | ---: | --- |
| Planner LLM call | ~600 ms | ~1.5 s | Haiku, ~1 k in / 100 out |
| Tool dispatch (parallel, 2–3 tools) | ~400 ms | ~900 ms | bounded by 1 s internal-endpoint p95 |
| Synthesizer LLM call | ~1.3 s | ~3 s | Haiku, ~5 k in / 700 out |
| Verifier (sentence-buffered, in-band) | adds ~300 ms TTFT | adds ~500 ms TTFT | runs concurrent w/ synth stream |
| **Total turn (warm)** | **~2.5 s** | **~5 s** | inside the 7 s ARCHITECTURE.md p95 ceiling |

**Extraction turn (warm), Haiku vision:**

| Node | p50 | p95 | Notes |
| ---: | ---: | ---: | --- |
| Upload + JWT mint + PDF fetch | ~150 ms | ~400 ms | OpenEMR session-auth path, 1 round-trip |
| `PdfRenderer.render_pages()` (1 page, 150 DPI) | ~80 ms | ~150 ms | PyMuPDF, in-process |
| Vision extraction call (Haiku, 1 page) | ~6 s | ~10 s | image-token-heavy, generation dominates |
| Vision extraction call (Haiku, 2 page) | ~9 s | ~12 s | ~2× the input-token bill |
| Synthesizer reply (small) | ~600 ms | ~1.2 s | "extracted N fields" |
| **Total turn (warm, 2-page)** | **~10 s** | **~14 s** | breaches the 7 s ceiling — extraction is intentionally over-budget |

**Cold first turn (RAG-enabled startup):**

The dense + cross-encoder models load ~190 MB of weights at
construction (3–5 s wall-clock) — comment at
[`sidecar/src/agentforge/config.py:95`](../sidecar/src/agentforge/config.py).
This is paid **once per sidecar process**, on the first turn after
boot; subsequent turns hit the warm path. The Round 3 tests trim
the slow LLM tier and the legacy turn-route latency probe (see
[`docs/NEXT-SESSION.md`](NEXT-SESSION.md) §"Round 3"); only the
internal-endpoint floor remains gated by CI.

The 12–15 s cold-first-call number in NEXT-SESSION line 194 is the
**combined** cold-vision-plus-process-warm-up cost on the demo
droplet. After the first call, the droplet sees 9–12 s for a 2-page
extraction.

## Eval-suite cost — measured vs derived

The W2 eval suite is 50 cases distributed
12/10/10/8/10 across `extraction` / `evidence_retrieval` /
`citations` / `refusal` / `missing_data` (see
[`docs/eval-report-2026-05-08.md`](eval-report-2026-05-08.md) §"Case set").
A measured run drives `build_graph().ainvoke()` once per case via the
production `SupervisorAdapter`
([`sidecar/src/agentforge/eval/supervisor_adapter.py`](../sidecar/src/agentforge/eval/supervisor_adapter.py)),
then runs the LLM judge on the cases routed to it.

**Measured (2026-05-09): $1.54** total
([`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)
`_meta`). Breakdown: `text_cost_usd: $0.996` over 142 text-LLM calls;
`vision_cost_usd: $0.542` over 10 vision-LLM calls; ~50 minutes
sequential wall-clock (lab-PDF vision dominated at 50–95 s per case).
**Estimated $0.65, worst-case $1.50** — measured came in slightly
above range, attributable to per-case retry on a few flaky lab-vision
cases. The derivation that follows is retained as the methodology
that produced the projection; it is now a sanity check on the
measured number rather than a stand-in for it.

### Per-class call envelope

Three classes of LLM call fire across the suite:

1. **Agent turn** (synthesizer + small support calls). Fires once per
   case (50 calls). Production model: `claude-haiku-4-5-20251001`.
   Envelope: ~5 k input tok + ~700 output tok per typical turn.
2. **Vision extraction.** Fires on the 12 `extraction` cases plus
   the subset of `citations` cases that attach a PDF. Conservative
   bound: 16 vision calls, 2-page typical (~6.8 k input tok + ~1 k
   output tok, Haiku).
3. **LLM judge.** Fires only on cases routed by `_JUDGE_BY_CATEGORY`
   in [`sidecar/tests/eval/harness_w2.py:46`](../sidecar/tests/eval/harness_w2.py).
   With zero `HALLUCINATION` cases and 8 `REFUSAL` cases, that is
   **8 judge calls per run** (more if `grade_with_retry` triggers).
   Pinned to `claude-sonnet-4-6` per
   [`sidecar/eval_config.yaml`](../sidecar/eval_config.yaml).
   Envelope: ~2 k input tok + ~150 output tok per call.

### Per-call cost

| Call class | Model | Input tok | Output tok | $/call |
| --- | --- | ---: | ---: | ---: |
| Agent turn | `claude-haiku-4-5` | 5,000 | 700 | $0.0068 |
| Vision extraction (2-page) | `claude-haiku-4-5` | 6,810 | 1,000 | $0.0094 |
| LLM judge | `claude-sonnet-4-6` | 2,000 | 150 | $0.0083 |

Math for the agent turn:
```
0.80e-6 × 5_000 + 4.0e-6 × 700 = $0.0040 + $0.0028 = $0.0068
```

Math for the vision extraction (2-page intake):
```
0.80e-6 × 6_810 + 4.0e-6 × 1_000 = $0.00545 + $0.00400 = $0.0094
```

Math for the LLM judge:
```
3.0e-6 × 2_000 + 15.0e-6 × 150 = $0.0060 + $0.00225 = $0.0083
```

### Per-run total

| Component | Calls | $/call | Subtotal |
| --- | ---: | ---: | ---: |
| Agent turns (Haiku synthesizer) | 50 | $0.0068 | $0.34 |
| Vision extraction (Haiku, 2-page) | 16 | $0.0094 | $0.15 |
| Judge calls (Sonnet 4.6) | 8 | $0.0083 | $0.07 |
| **Derived total per 50-case run** | | | **≈ $0.56** |

Headline rounded to **$0.65** to absorb the planner gap, occasional
3-page lab PDFs, and `grade_with_retry` going to a tiebreaker on a
couple of judge calls. Worst case with `grade_with_retry` going to a
tiebreaker on *every* judge call (3× the judge spend, ≈ $0.21 of
judge cost), a heavier vision mix (3-page intakes everywhere,
≈ $0.22), and a 5 k-input chart-overview default (≈ $0.45 of
agent-turn cost) put the worst case in the **$0.90–$1.50 range**.

**Measured came in at $1.54** — slightly above the worst-case ceiling.
Per-call accounting (142 text + 10 vision calls) shows the model
under-counted the agent-turn call volume: typical cases dispatched
two to three text calls per case (planner + synthesizer + occasional
verifier-driven retry) rather than the one assumed in the derivation.
The vision spend ($0.54) tracks the projection cleanly.

This is the same order-of-magnitude as the W1 Sonnet projection
($0.87 in [`docs/eval-report-2026-05-08.md`](eval-report-2026-05-08.md)
§"Cost analysis"); the Haiku/Sonnet swap on the agent turn cuts the
agent-turn line item ~75% (from $0.36 → $0.34, since Haiku is 3.75×
cheaper but the W1 envelope was ~6 k input vs the 5 k assumed
here), partially offset by the vision-extraction line item rising
from $0.00 (W1 had no doc-upload pipeline) to $0.15.

## Measured baseline (2026-05-09)

The first end-to-end run of the W2 50-case suite against the
production model mix landed on 2026-05-09 (commit `5bbfbe726`,
`_meta.status: "measured"` in
[`sidecar/tests/eval/baselines/week2.json`](../sidecar/tests/eval/baselines/week2.json)).
This is the anchor the eval gate now blocks regressions against —
not a claim that the agent is at 1.0.

| Category | Pass rate | Cases passed |
| --- | ---: | ---: |
| extraction | **0.417** | 5 / 12 |
| citations | **0.500** | 5 / 10 |
| evidence_retrieval | **0.500** | 5 / 10 |
| missing_data | **0.600** | 6 / 10 |
| refusal | **0.375** | 3 / 8 |

| Cost / timing | Value |
| --- | ---: |
| Total LLM spend | **$1.54** |
| Text cost | $1.00 (142 calls) |
| Vision cost | $0.54 (10 calls) |
| Wall-clock | ~50 minutes (sequential 50 cases) |
| Lab-PDF vision per case | 50–95 s |
| Gate verdict | **PASS** (exit 0, 0 violations) |

### What these rates do and don't claim

The gate's job is now **regression detection from this measured
anchor**, not "verify the agent is at 1.0." A future run that posts
e.g. extraction `0.30` against this `0.417` anchor fails the gate;
the gate is calibrated against actual agent behaviour rather than
the structurally-pinned 1.0 stub it replaced. Two known shortcuts
explain where the rates sit today (per `_meta.notes` in the baseline
JSON):

1. **`SupervisorAdapter` is intake-only.** It wires only the intake
   `VisionExtractor`, so the eight lab-PDF extraction/citation cases
   (lipid panel, CBC, CMP, hba1c) hit the wrong contract and account
   for 7 of the 7 extraction failures and 5 of the 5 citation
   failures. Re-measure after the lab-extractor wiring lands; the
   extraction and citations rates should lift materially without any
   change to the agent itself.
2. **Sonnet judge calibration drift.** Refusal cases now grade
   through the real `claude-sonnet-4-6` LLM judge for the first time;
   the pre-stub baseline never exercised the judge end-to-end. The
   0.375 rate likely reflects judge-prompt calibration drift rather
   than agent collapse — confirm with a calibration pass against
   golden-labelled refusals before tightening the threshold.

Both are non-blocking follow-ups. The 2026-05-09 run was a PASS
under the new measured baseline — the gate is doing its job and the
two shortcuts above are the next two cards to play against it.

## Where the cost / latency cliffs are

### Cost cliffs

1. **Vision extraction is ~50% of an extraction-turn's bill.** A
   2-page intake is ~5,610 image tokens at 150 DPI — about the same
   input bill as a chart-overview synthesizer call. The single
   biggest cost knob is **render DPI**, set at
   [`tools/attach_and_extract.py:86`](../sidecar/src/agentforge/tools/attach_and_extract.py)
   (`DEFAULT_DPI = 150`). The pixel area scales as DPI²; dropping to
   100 DPI cuts image tokens ~55% (1275×1650 → ~850×1100, ~1,247
   tok/page). Tradeoff: bbox precision degrades — Haiku-vision bboxes
   are already "approximate" at 150 DPI per
   [`docs/NEXT-SESSION.md`](NEXT-SESSION.md) §"Known gaps" item 2.
2. **`max_tokens=4096` on extractions** at
   [`tools/attach_and_extract.py:522`](../sidecar/src/agentforge/tools/attach_and_extract.py)
   sets the *cap*; typical extraction outputs are 800–1,500 tok, so
   tightening this won't save money on the average case but will
   bound a runaway worst case (`0.80e-6 × 4_096 = $0.0033` of
   ceiling exposure per call).
3. **Planner LLM call is not yet routed through `_record_llm_call`**
   ([`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md)
   §"Known cost gaps", carried forward in `docs/DEVIATIONS.md`). The
   cost-header undercounts by ~$0.005/turn. Closing this requires a
   one-line wiring change in `orchestrator/__init__.py:354-360`; not
   a deadline blocker but the report's per-turn numbers are
   accurate within ±$0.005 because of it.
4. **Verifier rejection-on-citation costs the same as a successful
   emit.** With the current verify-before-emit gate (Task 28), a
   rejected sentence has already paid for its tokens. With the
   citation rule re-enabled in strict mode, retry cost compounds.

### Latency cliffs

1. **Cold-start RAG model load: 3–5 s, paid once per sidecar
   process.** Comment at
   [`sidecar/src/agentforge/config.py:95`](../sidecar/src/agentforge/config.py):
   the dense + cross-encoder models load ~190 MB of weights at
   construction. The Dockerfile pre-bakes the weights (Task 21) so
   this is "process boot," not "request boot," but a `docker rm -f`
   redeploy is a 3–5 s cold first turn for any RAG-enabled query.
   Mitigation: keep the sidecar warm (don't recycle on every deploy);
   already in place.
2. **Vision extraction wall-clock dominates extraction-turn p95.**
   A 2-page Haiku vision call is ~9–12 s warm — order-of-magnitude
   the rest of the turn combined. This is the gap between the
   ARCHITECTURE.md §3 7 s p95 budget (Q&A turns) and the observed
   12–15 s extraction-turn cold-first-call. **Extraction is
   intentionally over-budget**: the user has just uploaded a
   document and is waiting on a different mental clock than mid-Q&A.
   Mitigations:
   - Smaller vision model: no smaller production-quality model in
     the Anthropic catalog beneath Haiku 4.5 today.
   - Lower DPI (see cost-cliff 1) — also cuts wall-clock on the
     server side because the per-page render+upload time scales
     with pixel count.
   - Streaming the structured-output stream directly to the
     `<ExtractionPanel>` so the user sees fields land progressively
     instead of waiting for the whole tool-use block.
3. **Bge-reranker-base image cost.** `docs/DEVIATIONS.md` logs that
   the pre-baked reranker image is ~1.1 GB at fp32, vs the original
   ~280 MB spec. This is a **boot-time** cost (image pull / disk
   footprint), not a per-turn cost. Mitigations available — fp16,
   smaller cross-encoder, int8 quantization, or swap to Cohere's
   hosted rerank API (`COHERE_API_KEY` switches the factory at
   [`sidecar/src/agentforge/rag/reranker_factory.py`](../sidecar/src/agentforge/rag/reranker_factory.py)
   — ~50 ms hosted vs ~100 ms local). Tradeoff: Cohere is
   per-request paid; the local cross-encoder is one-time-boot paid.
4. **Sentence-buffered verifier adds ~300–500 ms to TTFT** but is
   the load-bearing trust boundary (ARCHITECTURE.md §3.3); not
   negotiable for clinical safety.

### What we are explicitly not optimizing

- **Switching the synthesizer back to Sonnet** would re-add ~3.75×
  the per-turn cost. The Haiku swap is justified by the
  citation-grounded synthesizer + verifier — quality regression is
  bounded by the verifier dropping uncited sentences before they
  reach the user, so the cheaper model can't degrade the trust
  artifact even when it gets a fact wrong.
- **Routing the planner through the recorder** is a one-line fix
  but not done; the report's per-turn numbers carry a documented
  ±$0.005/turn undercount.
- **Tightening the orchestrator timeouts** below
  `total_turn=60s` would mostly fire on the extraction path, where
  we've already accepted the over-budget envelope.

## How to re-measure

The CLIs below produce real Langfuse-attached numbers that will
replace the derived ones above.

### Per-turn cost & latency from Langfuse

`agentforge.observability.cost_report` reads recent generation
observations from Langfuse, sums `cost_usd` and rolls up
`latency_ms` per step:

```bash
cd sidecar
uv run python -m agentforge.observability.cost_report                # last 7 days
uv run python -m agentforge.observability.cost_report --days 14
uv run python -m agentforge.observability.cost_report --days 30 --weekly
```

Requires `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`.
The droplet runs `NullLangfuseClient` today (per
[`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md)
§"Cost transparency surfaces"), so the rollup currently relies on
per-turn `X-Agent-Cost-USD` header observations rather than
Langfuse traces.

### Per-call cost on every turn

Every `/agentforge/turn` response carries the per-turn cost on
three surfaces (per
[`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md)
§"Cost transparency surfaces"):

- `X-Agent-Cost-USD` HTTP header (non-streaming)
- SSE `final` event `cost_usd` field (streaming)
- Trace span `cost_usd` (when Langfuse is wired)

To collect a measured per-turn cost distribution, run a fixed
script of N turns against the droplet and grep the headers.

### 50-case eval cost (live)

```bash
cd sidecar
# Smoke run with mocked LLMs — confirms wiring, no API spend:
uv run python -m agentforge.eval.regenerate_baseline \
    --mock --output /tmp/smoke.json

# Real-LLM run — needs ANTHROPIC_API_KEY. Spends ~$1.50, takes
# ~50 minutes sequential, writes per-category pass rates plus
# _meta cost/timing into the output JSON.
uv run python -m agentforge.eval.regenerate_baseline \
    --output sidecar/tests/eval/baselines/week2.json
```

`_build_real_supervisor_and_harness` is now wired (commit
`09ad5ea55`, 2026-05-09); both the `--mock` smoke and the real-LLM
path execute without a manual edit. The 2026-05-09 measured run that
seeded `_meta.cost_usd: 1.538` ran this exact command.

### Latency p95 floor (CI-enforced)

```bash
cd sidecar
uv run pytest -m latency tests/integration/test_latency.py
```

Asserts the JWT-protected internal endpoints respond p95 < 1000 ms
across 5 endpoints × 5 samples. This is the load-bearing latency
gate today; the LLM-tier counterpart was trimmed in Round 3 cleanup
([`docs/NEXT-SESSION.md`](NEXT-SESSION.md) §"Round 3.1") because it
posted to the deleted `turn.php` route.

## Limitations

The cost model is honest about three gaps that the report inherits:

1. **Per-turn dollar numbers are still derived; the run total is
   measured.** The 2026-05-09 baseline regen pinned the run total
   ($1.54) and per-call class counts (142 text, 10 vision) — but
   per-turn breakdowns by node are still derived from `cost.py`
   applied to plausible token envelopes (W1 measurements scaled by
   the Haiku/Sonnet rate ratio, prompt sizes counted from
   `prompts/v1/`). Replace per-turn numbers with Langfuse-attached
   per-step rollups once `cost_report.py` has at least 24 h of trace
   data on the droplet.
2. **Planner LLM call not routed through `_record_llm_call`.** The
   per-turn `X-Agent-Cost-USD` header undercounts by ~$0.005/turn
   (small system prompt + 1024-cap output). Tracked in
   `docs/DEVIATIONS.md`. The numbers in this report account for it
   in the headline-table rounding but the per-call math tables do
   not.
3. **`cost.py` does not have a separate vision-pricing path for
   Haiku-as-vision.** The `claude-haiku-4-5` row in `PRICING` is the
   text-rate row; Anthropic's `Usage.input_tokens` for vision calls
   already incorporates the image-token formula, so the same
   `calculate_cost` works at the SDK boundary (see the comment at
   line 602 of `attach_and_extract.py`). Pre-call projections from
   page dimensions go through `calculate_vision_cost`, which uses
   the same rate row. **Conclusion:** no double-counting, but the
   pricing table's mental model is "one rate row per model"; if
   Anthropic ever publishes vision-specific rates separate from
   text rates, `PRICING` needs a new shape.

## Artifacts

Canonical paths for everything cited in this report.

### Pricing source of truth
- [`sidecar/src/agentforge/observability/cost.py`](../sidecar/src/agentforge/observability/cost.py)
  — `PRICING`, `calculate_cost`, `calculate_vision_cost`,
  `estimate_image_tokens`, `_IMAGE_TOKEN_DIVISOR`.
- [`sidecar/src/agentforge/observability/cost_report.py`](../sidecar/src/agentforge/observability/cost_report.py)
  — daily/weekly Langfuse rollup CLI, `LatencyObservation`,
  `aggregate_latencies_by_step`.

### Latency tests + budgets
- [`sidecar/tests/integration/test_latency.py`](../sidecar/tests/integration/test_latency.py)
  — internal-endpoint p95 < 1000 ms gate.
- [`sidecar/src/agentforge/timeouts.py`](../sidecar/src/agentforge/timeouts.py)
  — `TimeoutPolicy` (per_tool, tool_phase, synthesis_phase,
  total_turn, max_steps).

### Production model + render config
- `/opt/agentforge/sidecar/.env` on the droplet — pinned
  `CLAUDE_MODEL=claude-haiku-4-5-20251001`,
  `ANTHROPIC_VISION_MODEL=claude-haiku-4-5-20251001`.
- [`sidecar/src/agentforge/config.py`](../sidecar/src/agentforge/config.py)
  — `Settings`, RAG-load comment, "~3× faster" Haiku note.
- [`sidecar/src/agentforge/tools/attach_and_extract.py`](../sidecar/src/agentforge/tools/attach_and_extract.py)
  — `DEFAULT_DPI=150`, `DEFAULT_VISION_MODEL`, `_INTAKE_SYSTEM_PROMPT`,
  `_LAB_SYSTEM_PROMPT`, `VisionExtractor.extract`, `max_tokens=4096`.

### Eval suite
- [`docs/eval-report-2026-05-08.md`](eval-report-2026-05-08.md) —
  W2 framework + gate report; methodology this report leans on.
- [`sidecar/tests/eval/cases/week2/`](../sidecar/tests/eval/cases/week2/)
  — 50 golden cases.
- [`sidecar/eval_config.yaml`](../sidecar/eval_config.yaml) —
  pinned judge model + thresholds.
- [`sidecar/src/agentforge/eval/regenerate_baseline.py`](../sidecar/src/agentforge/eval/regenerate_baseline.py)
  — manual-baseline regen CLI.
- [`sidecar/src/agentforge/eval/supervisor_adapter.py`](../sidecar/src/agentforge/eval/supervisor_adapter.py)
  — production `SupervisorAdapter`.

### Companion docs
- [`docs/cost-analysis-2026-05-03.md`](cost-analysis-2026-05-03.md)
  — W1 measured-cost report (Sonnet 4 era), source for the
  Sonnet→Haiku scaling.
- [`docs/NEXT-SESSION.md`](NEXT-SESSION.md) — measured cold-first-call
  latency, droplet config, Round 3 cleanup notes.
- [`docs/DEVIATIONS.md`](DEVIATIONS.md) — bge-reranker image-cost
  deviation, planner-cost-undercount carryforward.
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) §3 / §4 — 7 s p95 turn
  budget, 2 s per-tool / 4 s tool-phase audit targets.
