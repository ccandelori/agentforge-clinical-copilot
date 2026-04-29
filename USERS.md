# USERS.md — Target User and Use Cases

**Project:** AgentForge Clinical Co-Pilot
**Document date:** 2026-04-28
**Author:** Cameron Candelori
**Status:** Source of truth. Every capability proposed in `ARCHITECTURE.md` traces back to a use case here.

---

## Why this document exists

"Physicians need help finding information" is not a user. It is a thesis statement that has produced a thousand failed health-tech products. This document picks one real clinician, in one real moment of their workday, and defines the problem the Clinical Co-Pilot solves *for them, then*. Every architectural decision in the next document is judged against whether it serves this user in this moment — not against whether it sounds impressive in a demo.

## Target User

**Dr. Aisha Patel — Hospitalist.** Inpatient internal medicine, mid-sized community hospital. Works 7-on / 7-off, including overnight call. On call she is responsible for new admissions: patients flowing in from the ED, from outside transfers, or from direct admits, who she has never met. She is the attending of record for the duration of the admission.

A hospitalist is a deliberate choice for v1 over an outpatient PCP, because the hospitalist's interaction with the chart is structurally harder:

- The patient is **new to her**. She has no prior relationship, no mental model.
- The decision is **time-pressured but high-stakes**. She makes admit-status orders, restart-or-hold decisions on home medications, and contingency plans within minutes of the bed assignment.
- The data is **broad and shallow**. She needs a fast, cross-cutting read of an entire chart, not deep expertise in a single subspecialty.
- The tooling is **adversarial to speed**. EHR navigation between problem list, medications, allergies, labs, and recent notes takes 2–4 minutes per chart of clicking and waiting.
- She is **often physically tired** and reading on a small screen at 2 AM.

These conditions make the case for an agent the strongest. If we cannot help Dr. Patel, the use cases for an attending PCP with a 20-minute office visit are easier — not the other way around.

## The moment that matters

It is 2:14 AM. Dr. Patel is paged from the ED: a 78-year-old with shortness of breath, elevated troponin, history of CHF, being admitted to telemetry. The bed assignment hits her queue. She has approximately three to five minutes between the page and the moment she begins writing admission orders. In that window she needs to:

1. Understand who this patient is, why they are here, and what the admit story actually is.
2. Confirm that what she is about to order is safe given the patient's history (allergies, contraindications, prior reactions buried in old notes).
3. Decide which of the patient's home medications to restart, hold, or modify on admission.
4. Notice anything that has changed for this patient since their last hospital encounter (new diagnoses, new providers, new red flags).

These four needs are not satisfied by browsing the EHR. The data exists in the EHR. The work is in retrieving and synthesizing it under time pressure.

## How the agent enters Dr. Patel's workflow

Before any use case can trigger, the agent must know *which patient* Dr. Patel is asking about. The agent does not infer this from her message — that would be unsafe. Patient identity is established in one of two ways, both deterministic:

1. **In-chart launch (the canonical path).** Dr. Patel opens a patient record in OpenEMR. The agent is rendered as a Twig card on the patient summary page. The agent's session is bound to `(user_id = Patel, patient_id = <opened chart>)` at the moment the page renders. Every subsequent turn in the conversation inherits that binding. She does not need to name the patient; the chart context is the binding.

2. **Out-of-chart launch (refused by default).** If the agent is opened from a context with no patient bound (e.g., the inbox or a dashboard), the agent's first response is *always* a hard refusal: "I don't have a patient in context for this conversation. Please open a patient chart first." It does not attempt to disambiguate from natural language, even if Dr. Patel says "what's going on with Mr. Johnson." Identity ambiguity is a hard stop, per our verification rules.

When the agent is bound to a patient outside Dr. Patel's normal panel — which is the common hospitalist case — the **break-the-glass flow** triggers automatically. Detail in UC-1, since hospitalist admits are the canonical instance.

## Latency targets — consistent format

All use cases below use the same two-part format:

- **First useful token by Xs (p95)** — when streaming begins; what Dr. Patel sees moving on screen.
- **Complete response by Ys (p95)** — when the full response is rendered, including all citations.

This split matters because the two are solved differently in the architecture: first-token latency is a function of the LLM provider and prompt size; full-response latency is a function of tool-phase parallelism and verifier overhead. Streaming partial answers buys time for the verifier without delaying the user's read.

---

## Use Cases

Each use case below specifies: the concrete moment, the data the agent must access, why a conversational agent is the correct interface, what counts as success, what counts as failure, and what citation evidence the agent's response must include. Every use case maps to a capability that `ARCHITECTURE.md` will commit to building.

### UC-1 — Admit Synthesis

> "Give me a quick summary of this patient — why are they here, what do I need to know right now."

**Trigger.** Bed assignment notification arrives. Dr. Patel opens the patient record for the first time, which binds the agent's session to that patient. Three to five minutes until orders. Because she has no prior relationship with this patient, the **break-the-glass flow** also triggers (see *Break-the-glass design* below).

**Data the agent must access.**
- Demographics (`patient_data`)
- Active problem list (`lists` where type = `'medical_problem'`)
- Active medications (`prescriptions` where `active = 1`)
- Allergies (`lists` where type = `'allergy'`)
- Most recent encounter and its chief complaint (`form_encounter`, `form_vitals`, `pnotes`)
- Labs from the last 90 days. Lab retrieval requires the `procedure_order → procedure_report → procedure_result` join chain — orders, reports per order, and individual result rows per report. The agent's lab tool wraps this join; details live in ARCHITECTURE.md.
- Vital trend from the last 30 days (`form_vitals`)

**Why a conversational agent is the right interface here, and not something else:**
- A **dashboard** requires Dr. Patel to know what to look at. At 2 AM, knowing what to look at *is* the work. The agent collapses "navigation + reading + synthesis" into one step.
- A **chart-style view** shows raw numbers without prioritization. Dr. Patel does not need a graph of every lab — she needs the lab that is out of range and the medication that interacts with it surfaced first.
- A **sorted list** of recent encounters does not answer "why are they here." It only orders documents.
- The synthesis itself — connecting an elevated troponin to a CHF history to a missed diuretic dose three weeks ago — is the value. That synthesis is what an LLM is for.

**Latency target.** First useful token by 3s (p95). Complete response by 7s (p95).

**Success criteria.**
- Every clinical claim has an inline citation referencing the source record (`encounter_id`, `lab_id`, `prescription_id`, plus date).
- The response covers: chief complaint, active problems prioritized by likely admission relevance, current home medications, allergies, recent labs out of range, and one or two notable changes from the previous encounter if applicable.
- The response does not invent. If the chart says nothing about cardiac history, the response says nothing about cardiac history. "Not on file" is a valid and required answer for missing data.

**Failure modes (the response must degrade rather than crash):**
- A tool times out → the agent returns a partial summary with a degradation notice naming the missing section ("I retrieved meds, allergies, and recent labs, but encounter notes timed out — review chart for full context").
- A claim cannot be grounded in a source → the verifier drops it from the response. The agent does not retry, because retries are how a model hallucinates a justification.
- The patient record is mostly empty → the agent says so plainly. It does not pad.

**Citation requirements.** Every factual sentence ends with a structured reference: `[encounter #38241, 2026-04-12]`, `[lab: HbA1c=9.4, 2026-03-30]`, `[Rx: lisinopril 20mg, started 2024-08-15, active]`.

**Break-the-glass design (specific to UC-1).** Hospitalist admit is the canonical break-the-glass instance: the user is accessing a patient outside their normal panel, with legitimate clinical need, and a documented reason must be captured. The agent does not initiate break-the-glass — OpenEMR's `BreakglassChecker` already determines whether the user is in a break-the-glass-eligible role and flags the access. The agent operates *inside* the boundary the user has already crossed and adds two things:

1. **Reason capture in the agent UI.** Before the agent processes Dr. Patel's first turn, a small banner appears in the agent panel: *"You are accessing a patient outside your assigned panel. Reason for access:"* with three pre-set options (Admit, Consult, Cross-cover) and a free-text field. The admit context is pre-selected when the agent detects the patient was just admitted in the last 30 minutes. She can confirm or override. The reason is written into `log.comments` via OpenEMR's existing audit infrastructure — no new audit table.
2. **Audit trail propagation.** Every tool call the agent makes within this session carries the break-the-glass flag and the captured reason in its structured log entry, mirroring OpenEMR's `gbl_force_log_breakglass` semantics. This way the agent's per-call audit trail is consistent with OpenEMR's chart-access audit trail; an investigator sees one coherent story, not two parallel ones.

If Dr. Patel cancels or refuses to provide a reason, the agent refuses to proceed. This is consistent with our HITL stance: blocking is the correct posture for any access where a documented reason is part of the legal/ethical contract.

---

### UC-2 — Pre-Order Safety Check

> "Is there anything in their history that would contraindicate starting a beta-blocker?"

**Trigger.** Dr. Patel is about to enter an order. Before she signs it she wants a focused read of the chart against this specific intervention.

**Data the agent must access.**
- Allergies and prior adverse reactions (`lists` allergy + free-text note search)
- Active medications for interaction screening (`prescriptions` active = 1)
- Problem list (`lists` problem)
- Recent free-text notes for documented intolerances or prior trials (`pnotes`, `form_clinical_notes`)
- Optionally: recent vitals if relevant to the proposed med class (e.g., bradycardia for beta-blockers)

**Why a conversational agent is the right interface here, and not something else:**
- A **structured allergy form** captures the intentionally-coded reactions only. Real contraindications are often buried as free text in old notes — "patient reports developing dry cough on lisinopril in 2019, switched to losartan." An interactive search bar cannot know to look for that. The agent reading natural language across structured *and* unstructured data is the differentiator.
- A **medication interaction database** (UpToDate, Lexicomp) tells Dr. Patel what beta-blockers interact with — generally. It does not tell her what *this patient* has reacted to historically.
- A **problem-list filter** does not catch things outside the problem list, which in practice is incomplete on most charts.
- The shape of the question — "anything in their history that would contraindicate X" — is irreducibly conversational. There is no fixed-shape form for it.

**Latency target.** First useful token by 2s (p95). Complete response by 5s (p95). UC-2 is faster-budget than UC-1 because the answer scope is narrower (one focused question, one focused chart-search) and Dr. Patel is mid-order — interruption tolerance is lower.

**Success criteria.**
- The agent answers either: (a) "No specific contraindication on file" with citations to what was checked, or (b) "Yes — [specific concern]" with the source: e.g., "patient noted dry cough on lisinopril, see encounter #21134, 2019-06-04."
- The agent does **not** dispense generic clinical advice ("beta-blockers are contraindicated in asthma"). It answers about *this patient*. If the chart lacks the data needed to answer, it says so.

**Failure modes:**
- The agent has no good evidence either way → it says "I did not find a specific contraindication for [med X] in this patient's record (checked allergies, problem list, recent notes since 2024). This is not a clinical clearance." It explicitly disclaims its scope.
- Free-text note search returns ambiguous matches → agent surfaces both, does not pick a winner.

**Citation requirements.** Same as UC-1, with the added rule: any "no contraindication found" response must list the data sources actually checked. The agent's confidence is bounded by what it could see.

---

### UC-3 — Change Detection

> "What's changed for this patient since their last hospitalization?"

**Trigger.** Patient has been admitted before, possibly at this hospital, possibly elsewhere. Dr. Patel wants the *delta*, not the full history.

**Data the agent must access.**
- The previous encounter and its discharge problem list / med list (`form_encounter`, `lists` snapshots if available, discharge summary in `pnotes`)
- The current problem list and active meds
- New diagnoses added to `lists` since the prior encounter
- New medications started since the prior discharge
- New allergies/reactions documented since the prior discharge
- Labs that have moved out of range since the last visit
- Any provider changes (new specialist on the care team)

**Why a conversational agent is the right interface here, and not something else:**
- A **diff view** of two encounters could in principle be built, but "changed" is a heterogeneous concept: structural changes (new diagnosis), continuous-trend changes (BP creeping up), categorical-state changes (new med, dose adjustment), and contextual changes (new specialist seeing them). One UI cannot present all four at the right level of abstraction.
- A **dashboard with trend graphs** flattens semantic changes into visual deltas, losing the clinical interpretation. "BP up by 15 mmHg" is a number; "BP rising on a stable regimen, suggesting non-adherence or progression" is the read.
- The output of this query is fundamentally a short briefing, not a record. A clinician at 2 AM does not want to drive an interface; they want to be told.

**Latency target.** First useful token by 3s (p95). Complete response by 7s (p95). Same envelope as UC-1 — UC-3 needs to compare two windows of data, but the response surface is shorter (deltas only), so the budgets balance.

**Success criteria.**
- The agent produces a short structured response: *new problems*, *medication changes*, *labs trending out of baseline*, *new providers/care-team changes*, *anything else flagged in notes*.
- Each delta cites both the prior reference point (encounter / value / date) and the current state.

**Failure modes:**
- No prior encounter on file → agent says so plainly and offers what it can: "First encounter on record. I cannot compute a delta — here is what is currently on file."
- One side of the comparison has incomplete data → agent flags the asymmetry rather than silently treating gaps as "no change."

**Citation requirements.** Every delta must include both the *from* and *to* sources, both with dates and record IDs.

---

### UC-4 — Conversational Follow-Up

> "Tell me more about that troponin trend." / "What did the discharge summary from her last admit actually say?" / "Of those meds, which are renal-cleared?"

**Trigger.** The agent has just produced a response (UC-1, UC-2, or UC-3). Dr. Patel reads it on screen and has a narrowing question — about a specific finding, a specific document, or a specific subset of what was just surfaced. She asks the follow-up *without restating which patient*, *without restating the original question*, and *without naming the structured field she's drilling into*.

**Why this is its own use case.** UC-1 / UC-2 / UC-3 each describe a single-shot query. The hospitalist workflow is not single-shot. After Dr. Patel reads an admit synthesis (UC-1), she will routinely have one or two narrowing questions before she signs the first order — that is how clinicians read records. If the agent forces her to either re-issue the full query (wasteful) or scroll back through the prior response (which defeats the agent's purpose), the agent has failed at the very moment it was meant to help. Multi-turn conversation is therefore not a "nice to have" — it is the shape of the workflow itself.

**Why a conversational agent is the right interface here, and not something else:**
- The previous-turn context is what makes the follow-up legible. "Tell me more about that troponin trend" only makes sense if the agent remembers what "that" refers to. A stateless query interface cannot do this.
- A **search refinement UI** could in principle allow drilling into a previous result, but it would require Dr. Patel to navigate widgets — at the precise moment when she's already mid-order on a different screen.
- The data behind the follow-up is not always in a different place than the original query — sometimes the answer is already in the prior response and just needs to be expanded or rephrased. Sometimes it requires a deeper tool call. The agent decides; the user does not.

**Examples and what each implies architecturally:**
- *"Tell me more about the troponin trend."* — narrows into already-retrieved labs; may not require a new tool call. Agent expands inline.
- *"What did the discharge summary from her last admit actually say?"* — requires a new tool call to fetch the full text of a specific note. New retrieval, scoped by the prior turn's encounter reference.
- *"Of those meds, which are renal-cleared?"* — requires reasoning over the previous turn's medication list against domain knowledge the LLM brings. May or may not require a new tool call (e.g., for current eGFR).
- *"Was that lab fasting?"* — narrows into a lab record's metadata. New tool call against the same `procedure_result` row.

**Success criteria.**
- The agent maintains session memory of the prior turn's patient binding, retrieved data, and citations. Follow-up responses do not re-derive what was already established.
- Latency is the same envelope as the use case being followed up on (UC-1/2/3 budgets apply per turn, not cumulatively).
- Identity and authorization remain bound from the original turn — the patient cannot change mid-conversation. A follow-up that names a different patient triggers the identity-ambiguity hard stop.

**Failure modes.**
- Session memory is lost (e.g., backend restart) → agent says so plainly: "I've lost the context of the prior turn — please re-state the question." Does not silently lose state and answer wrongly.
- Follow-up references a citation that has since changed (rare but possible if the chart was updated) → agent flags the change rather than answering against stale memory.

**Citation requirements.** Same as the use case being followed up on. Citations from the prior turn are reusable but must be re-attributed in the new response so the user does not have to scroll back.

**Bounded by design.** The agent does *not* support unbounded conversational drift. After roughly 6 conversational turns within a single patient session, the agent suggests resetting context. This is a soft limit that protects both quality (long contexts degrade verifier signal) and cost (token usage). The cap will be tunable based on eval results.

---

## What "useful" means for this user

For Dr. Patel, the agent is useful when, at 2:14 AM:

- It costs less time to ask the agent than to navigate the chart for the equivalent answer.
- The answer is grounded in this patient's data, with traceable citations she can click through and verify.
- The agent is honest about what it doesn't know and what it didn't check.
- The agent never adds a new failure mode to her workflow. It either provides value or gets out of the way; it does not introduce a new place for an error to occur.

For Dr. Patel, the agent is **not** useful — and is in fact harmful — when:

- It produces confident statements that are wrong or unverifiable. A confident hallucination is worse than no agent.
- It pads its response to look thorough.
- It dispenses generic clinical guidance unrelated to the patient.
- It surfaces protected information she is not authorized to read (a sensitive psychiatry note, a substance-abuse-treatment record under 42 CFR Part 2). Even at 2 AM, even if she has break-the-glass access, the agent must respect the same boundaries the underlying system enforces.

These four "not useful" conditions are why the architecture in the next document treats verification, source attribution, and authorization-aware data retrieval as load-bearing requirements, not nice-to-haves.

---

## Other personas — deferred, but architecturally anticipated

The Clinical Co-Pilot's v1 ships for Dr. Patel and only Dr. Patel. The architecture, however, is designed so that the following personas can be added without rewrite. Each represents a non-trivial extension that the auth model and tool layer must be able to absorb:

| Persona | Story shape | What this adds to the architecture |
|---|---|---|
| **Dr. Sarah Chen** — Attending PCP, 20 patients/day | "What's changed with Maria since her last visit?" | Tighter latency budget (90s window). Same data, lower tolerance for slowness. |
| **Dr. Marcus Webb** — Resident, supervised | Wants to draft notes and propose orders, some require co-signature | Per-capability HITL: write actions that require co-sign must block, not advise. Auth model needs supervised-by relationship. |
| **Jamie Torres, RN** — Floor nurse | "Is this patient on antihypertensives?" but cannot see psych notes | Record-level sensitivity enforcement at the agent's tool layer. Different scope of read access per role. |
| **Carlos Rivera, MA** — Medical assistant | Voice-driven vitals entry through the agent | Write-back capability with explicit confirmation UX, plausibility validation, distinct audit trail. |
| **Covering physician** | Picks up Dr. Chen's patient while she's on vacation | Attending-only / relationship-based sensitivity flags that travel with records. Break-the-glass with reason capture. |

These are listed not because v1 will serve them, but because the architecture must not foreclose serving them. ARCHITECTURE.md will document which extension points are designed for them and which are explicitly out of scope until v2.

---

## Source-of-truth crosswalk

Every agent capability in `ARCHITECTURE.md` must cite a use case here. The reverse is also true: if a use case does not appear here, no architectural complexity should be incurred for it. The capability inventory we expect:

| Capability | Use case anchor |
|---|---|
| Multi-source patient data retrieval (demographics, problems, meds, allergies, labs, encounters, notes, vitals) | UC-1 |
| Source-attributed synthesis with inline citations | UC-1, UC-2, UC-3, UC-4 |
| Free-text note search across the patient record | UC-2 |
| Cross-encounter delta computation | UC-3 |
| Multi-turn session memory (prior-turn patient binding, retrieved data, citations) | UC-4 |
| Bounded conversation (turn cap before context reset) | UC-4 |
| Domain-constraint verification (a med claim must trace to a real `prescriptions` row, etc.) | UC-1, UC-2, UC-3, UC-4 |
| Sensitivity-aware filtering at the tool gateway | UC-2 (pre-emptive, prevents leaking psych notes into a generic safety check) |
| Patient identity binding from chart-launch context; hard stop on out-of-context launch | All — precondition before any UC can trigger |
| Break-the-glass reason capture and audit-trail propagation | UC-1 (canonical hospitalist admit instance) |
| Graceful degradation: timeouts return partial answers with degradation notices | UC-1, UC-2, UC-3, UC-4 |
| Identity-ambiguity hard stop (refuse if a follow-up names a different patient) | UC-4 (and any future use case where patient context could shift) |
| Streaming response (first-token vs full-response budget separation) | All — encoded in latency targets |

Anything not in this table that ARCHITECTURE.md proposes building requires an explicit justification in this document first.
