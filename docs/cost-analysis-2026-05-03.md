# AgentForge Cost Analysis — 2026-05-03

Per-turn cost data collected from the live droplet
(`https://<droplet>:9300/`) during demo sessions. Cost is
emitted on every turn via the `X-Agent-Cost-USD` HTTP response
header and the SSE `final` event's `cost_usd` field. Aggregated
per-turn cost is computed in
[`sidecar/src/agentforge/observability/cost.py`](../sidecar/src/agentforge/observability/cost.py)
from token-level counts using a Claude Sonnet 4 pricing table.

## Per-turn observed costs

| Turn type | Tools called | Tokens (in / out) | Cost (USD) |
| --- | --- | --- | --- |
| Active medications query | `get_active_medications` | ~3.5k / ~700 | **$0.0263** |
| Active medications query (re-run) | `get_active_medications` | ~3.4k / ~700 | **$0.0259** |
| Full chart overview | demographics + problems + meds + allergies + labs + vitals + recent encounters | ~12k / ~2.5k | **~$0.060** |
| Budget-exceeded partial (pre-fix) | partial — turn cut at 7s | ~1.2k / ~200 | $0.012 |

**Pricing reference** (Claude Sonnet 4, May 2026):

- Input: $3 per 1M tokens
- Output: $15 per 1M tokens

## Cost per clinical use case

Assuming a hospitalist's chart-prep workflow (per
[`USERS.md`](../USERS.md) §Use cases):

| Use case | Avg turns | Cost per session |
| --- | --- | --- |
| Admit synthesis (UC1) | 1 chart overview + 2-3 follow-ups | **~$0.15** |
| Contraindication check (UC2) | 1 targeted query | ~$0.03 |
| 90-day delta (UC3) | 1 query w/ 3 tools | ~$0.05 |
| Follow-up question (UC4) | 1 short turn | ~$0.02 |

A clinician handling 5 admits per shift with 1 follow-up question
per admit: **~$1.00 / shift**.

## Daily / monthly projections

| Scale | Sessions / day | Daily cost | Monthly cost |
| --- | --- | --- | --- |
| Single clinician | 5 admits × 4 turns | $0.60 | ~$13 |
| 10-clinician unit | 50 admits × 4 turns | $6 | ~$130 |
| 100-clinician hospital | 500 admits × 4 turns | $60 | ~$1,300 |
| 1,000-clinician health system | 5,000 admits × 4 turns | $600 | ~$13,000 |

These are LLM-API costs only. Infrastructure (sidecar VM, Redis,
optional self-hosted Langfuse) adds ~$50-200/month per environment
and does not scale linearly with usage.

## ROI sketch

Hospital admission chart-prep is ~10-15 minutes of attending time
per admit (per the user research in
[`USERS.md`](../USERS.md) §Persona). Conservatively assuming 5
minutes saved per admit at a $200/hr blended hospitalist rate:

- 5 admits × 5 min × $200/hr = **$83 saved per clinician per shift**
- 100 clinicians × 250 shifts/year = **$2.1M saved per year**
- AgentForge LLM cost at that scale: **~$15,000 / year**
- ROI: ~140×

This is intentionally conservative — the comparator in the user
research is not "save 5 minutes" but "ask the chart a question I
wouldn't have time to ask manually," which is a different kind of
value not captured in time-saved math.

## Cost transparency surfaces

Every turn emits cost on three surfaces:

1. **`X-Agent-Cost-USD` HTTP response header** — non-streaming
   path; visible in browser DevTools → Network → Response Headers.
2. **SSE `final` event `cost_usd` field** — streaming path; visible
   in DevTools → Network → EventStream.
3. **Trace span** (when Langfuse keys are wired) — per-call
   token counts and aggregated per-turn cost via
   `record_llm_call` and `_TURN_COST_VAR` ContextVar in
   [`orchestrator/__init__.py`](../sidecar/src/agentforge/orchestrator/__init__.py).

The CLI tool
[`agentforge.observability.cost_report`](../sidecar/src/agentforge/observability/cost_report.py)
produces a daily / weekly cost rollup from Langfuse traces. It
requires `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, and
`LANGFUSE_SECRET_KEY` — the demo droplet runs `NullLangfuseClient`
today, so the rollup is currently produced from per-turn header
observations rather than the CLI.

## Known cost gaps (carryforward)

- **Planner LLM call is not yet routed through `_record_llm_call`**
  ([`orchestrator/__init__.py:354-360`](../sidecar/src/agentforge/orchestrator/__init__.py)),
  so the per-turn cost counter undercounts by ~$0.005 per turn
  (small system prompt + 1024-cap output). Tracked in
  [`docs/DEVIATIONS.md`](DEVIATIONS.md).
- **Verifier rejection-on-citation costs the same as a successful
  emit** — the LLM call bills regardless of whether the verifier
  accepts the output. With the verifier disabled-by-fail-open
  policy from today's demo polish, this is a non-issue, but it
  matters once the strict citation rule is re-enabled.
