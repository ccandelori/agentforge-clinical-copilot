# Pre-Search Summary — AgentForge Clinical Co-Pilot
Date: 2026-04-27

## Phase 1: Constraints

### Domain
- Healthcare / clinical, integrated into OpenEMR fork.
- HIPAA-regulated; assume BAA with all LLM providers (per project rule).
- v1 user: hospitalist (Dr. Patel persona) — 2 AM admit, no prior patient relationship.
- v1 use case: "Quick summary of this admitted patient — why are they here, what do I need to know right now."
- Architecture must be extensible to attending, resident, RN, MA, covering MD without rewrite. v1 ships ONE persona end-to-end.
- Implies: break-the-glass access pattern + audit trail required from day 1.

### Scale & Performance
- Target: mid hospital (~100s users).
- Latency: p50 ≤ 3s, p95 ≤ 8s (hard ceiling: 7s agent deadline + 1s variance buffer).
- Cost ceiling: open question.
- Query volume: open question (Fermi: 20 patients/day × ~3 queries × ~100 physicians = ~6,000/day).

### Reliability
- Verification non-negotiable: source attribution + domain-constraint enforcement (locked defaults below).
- HITL: per-capability — read-summary advisory, write-back/co-sign blocking. v1 only implements advisory read.
- Audit retention: HIPAA-compliant (target 6yr for clinical records).

### Team
- Open question (solo vs. team, framework familiarity, eval comfort, 6-month owner).

## Phase 2: Architecture

### Framework
- Multi-agent with session memory (per-encounter).
- Direct Anthropic/OpenAI SDK + custom orchestration + separate verifier pass.
- LangGraph favored over vanilla LangChain due to native parallel tool dispatch (required by time budget).

### LLM
- v1: cloud provider (Claude or GPT-4o) under assumed BAA.
- LLM interface designed as **abstraction layer** so backend can swap to self-hosted vLLM in production without agent code changes.
- Tier: mid-tier (Sonnet / 4o) for synthesis; cheaper model for routing if needed.
- Context window: open (depends on typical patient data payload size — measure during build).

### Tools (v1, against mocked OpenEMR layer first)
- get_patient_demographics, get_active_problems, get_active_medications, get_allergies, get_recent_labs, get_recent_encounters, get_recent_notes (sensitivity-filtered), get_vitals_trend, log_break_the_glass_access.
- All tools return structured error objects, never throw.
- Authorization at gateway, not per-tool. Single chokepoint.
- Tool list to be reviewed with clinical SME before lock.

### Observability
- Self-hosted Langfuse.
- Metrics: per-step trace, latency, tool failures, token cost (mandatory four), plus auth decisions and break-the-glass events.
- Alerts: WARN-tier to Slack/email for MVP. PAGE-tier deferred to production.

### Evals
- Ground truth: OpenEMR sample data (programmatic — claims must trace to source rows).
- Method: programmatic grounding check + LLM-as-judge for clinical relevance + human spot-checks.
- CI regression gate required.
- Adversarial set: 5 categories — prompt injection in note text, auth-boundary attempts, hallucination probes, missing-data, conflicting data.

### Verification (locked v1 defaults)
- **Source attribution**: every factual claim has an inline citation (record id + date).
- **Domain constraints**:
  1. Med name in response must match a row in patient's medications table.
  2. Lab values match stored values within tolerance.
  3. Note content only surfaced if user authorized (sensitivity flags + role).
  4. Diagnoses traced to problem list or documented assessment.
  5. No counterfactuals — missing data → "not on file," never "patient denies."
- **Confidence/escalation**: drop unverifiable claims, flag stale (>30d labs), surface conflicts without picking a winner.
- Verifier may run during streaming synthesis (not serial post-pass) to fit time budget.

## Phase 3: Operations

### Failure modes (locked policies)
- Per-tool timeout: 2s hard cutoff. Returns structured error.
- Tool phase budget: 4s. Synthesis proceeds with whatever returned.
- Total agent deadline: 7s. Returns partial response with degradation notice if hit.
- Max steps: 5 tool invocations / turn; 12k tokens to synthesis input (priority-truncate: structured > free-text, recent > old); 3 retries with 200ms exp. backoff.
- Provider outage: primary → same-tier secondary (Claude ↔ GPT-4o) → hard-fail with clean message. Background health check every 30s. No degraded-mode local fallback in v1.
- Ambiguity:
  - **Identity** ("which patient?") → hard stop, ask specifically.
  - **Intent** ("how's she doing?") → best-effort with interpretation stated at top of response.
  - **Out-of-scope / unauthorized** → hard refuse with specific explanation + break-the-glass path.

### Security
- Patient data treated as untrusted input (prompt injection vector).
- Per-environment API keys with usage logs (auditable demonstration that PHI was sent under controlled conditions).
- Audit log fields: timestamp, user_id, role, patient_id, tool_calls[], hashed inputs, outputs, break_glass_flag, override_reason, verifier_result, latency, tokens, cost.
- Session memory: encrypted at rest, scoped to (user_id, patient_id), TTL'd. Default 60–90 min ("encounter window") preferred over 4h ("shift") for tighter PHI exposure surface — pick one and document. Redis or Postgres-with-TTL acceptable; verify BAA tier with provider.
- Observability stores metadata only, not message content.

### Testing
- Unit tests on each tool (mocked OpenEMR responses).
- Integration tests: replay-cassette LLM responses for deterministic CI.
- Adversarial set: 5 categories above.
- Regression locks: 5–10 canonical Q&A with expected answer shape.

### Open source
- Skip. OpenEMR is GPL-3; redistribution implications apply if/when published.

### Deployment
- Hosting: cloud (fly.io / railway / render) for MVP demo.
- LLM: cloud API (BAA assumed) for MVP. Production-hardened path documents self-hosted vLLM topology + hardware + security rationale.
- Prompt versioning: prompts as files in git; version constant tagged in every Langfuse trace; rollback = git revert + redeploy.
- Staging: deferred to post-MVP.

### Iteration
- Feedback v1: out-of-band (demo viewing). Inline thumbs in v2.
- Failed-case curation: weekly review, anything misgrounded or leaking goes into eval set.
- Owner in 6 months: open question.

## Open Questions
- [ ] Cost ceiling per query and monthly cap.
- [ ] Expected query volume (Fermi-estimate before build, validate with logs after).
- [ ] LLM context window need (measure typical patient payload).
- [ ] Team / framework familiarity / domain SME access / 6-month owner.
- [ ] Final tool list — review with clinical SME.
- [ ] Session TTL: 60–90 min vs 4h (pick + document).
- [ ] Self-hosted Langfuse host topology.

## Identified Risks
- **Time budget is tight**: parallel tool dispatch + streaming verifier are required, not optional. Sequential implementation will miss p95.
- **Sensitivity filtering at the tool layer is the load-bearing security control**. Bypass = HIPAA breach. Needs adversarial test coverage on day 1.
- **Cloud LLM is a production blocker for some hospitals** even with BAA. Abstraction layer is the mitigation; not building it = costly v2 rewrite.
- **OpenEMR data quality is unknown** — missing fields, inconsistent formats, duplicate records will become agent failure modes (PDF flags this in the Stage 3 audit).
- **"Extensible to all 6 personas"** is easy to over-engineer. Discipline: ship hospitalist read-summary; auth model designed for extension; resident co-sign / MA write-back NOT in v1.
- **Prompt injection via patient note content** is a real and untested attack surface in clinical agents. Adversarial set must include this.

## Next Steps
1. Stage 1: get OpenEMR running locally with sample patient data; document setup.
2. Stage 2: deploy fork to fly.io/railway/render; submit URL.
3. Stage 3: complete AUDIT.md (security, performance, architecture, data quality, compliance) — surface what the data layer can/can't support.
4. Stage 4: complete USERS.md — hospitalist persona + 1–3 specific use cases, each with "why an agent" answer.
5. Stage 5: complete ARCHITECTURE.md using AUDIT.md findings + this presearch as input. Lead with ~500 word executive summary.
6. Resolve open questions before Tuesday submission where feasible; explicitly defer the rest with rationale.
