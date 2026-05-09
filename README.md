# AgentForge — Clinical Co-Pilot for OpenEMR

A chart-aware AI agent embedded in [OpenEMR](https://open-emr.org) with
a Vue 3 patient dashboard and a multimodal evidence pipeline. Clinicians
upload an intake form or lab PDF; the agent extracts structured fields
through a vision LLM, surfaces them inline with **a clickable bounding
box back to the source pixels**, and persists them through OpenEMR's
canonical service layer with the audit trail intact. Chart Q&A across
problems, meds, labs, vitals, allergies, immunizations, procedures, and
notes runs through the same drawer, with every assistant claim grounded
to a citation that resolves to a row in OpenEMR. Built as a fork of
OpenEMR for the Gauntlet AI cohort W1 + W2 deliverable.

## Live demo

| URL | Notes |
| --- | --- |
| [`https://143.244.157.90:9300/dashboard/`](https://143.244.157.90:9300/dashboard/) | Vue 3 patient dashboard with embedded AgentForge drawer — **self-signed cert**, browser warns; click through |

Login: `admin` / `LESZoHXpasV3LL9LP5uQjWs2`. The OpenEMR default
`admin/pass` was rotated to a 24-char value to deter random
brute-force scanners. The credential is intentionally public because
the droplet is a sandbox seeded with synthetic Synthea data only — no
real PHI lives there.

**Demo personas** (seeded via `scripts/seed-demo-patients.php`,
idempotent on `pubpid`):

| Persona | MRN | Intake form | Use for |
| --- | --- | --- | --- |
| **Margaret Chen** | `MRN-2026-04481` | `p01-chen-intake-typed.pdf` | Primary — typed PDF + bbox citation overlay story |
| James Whitaker | (auto) | `p02-whitaker-intake.pdf` | Fallback if Chen flakes |
| Sofia Reyes | (auto) | `p03-reyes-intake.png` | PNG path (does **not** render in DocumentViewer — PDF.js only) |
| Robert Kowalski | (auto) | `p04-kowalski-intake.png` | PNG path (same) |

**Stick to typed PDFs (Chen, Whitaker)** for the bbox-overlay
demo path. Full runbook in
[`docs/w2-demo-script.md`](docs/w2-demo-script.md).

## Architecture in one diagram

```
Browser  ─── /dashboard/* ───►  Vue 3 SPA            (vue-ui/)
         ─── /auth/*      ───►  AgentForge sidecar   (sidecar/, FastAPI + LangGraph)
         ─── /api/*       ───►       │                   ├── BFF: OAuth2 token never enters JS
                                     │                   ├── orchestrator + tool catalog
                                     │                   ├── vision extractor (Haiku)
                                     │                   └── persister (extractions → OpenEMR)
                                     ▼
                                OpenEMR PHP            (interface/, src/, +AgentForge module)
                                  ├── FHIR R4 API     (read; user/* scopes)
                                  ├── OAuth2 server   (/oauth2/default)
                                  └── Internal* JWT   (writes; sidecar-only entry)
```

Same-origin in production via Apache reverse proxy
(`docker/openemr-proxy/agentforge-proxy.conf`); dev-easy uses Vite's
proxy. The dashboard never speaks to OpenEMR's FHIR endpoint
directly; every `/api/fhir/*` and `/api/agent/*` call rides the
sidecar BFF, which attaches the user's bearer token from a Redis
session keyed by an HttpOnly cookie. The OAuth2 access token never
touches JavaScript.

The OpenEMR PHP backend is **untouched apart from a small set of
JWT-scoped internal endpoints** added inside the AgentForge module
(`Internal*Controller.php`). No legacy controllers, no core schema,
and no upstream FHIR routes were modified.

## Architectural defense (one paragraph each)

The W2 surprise challenge graded the dashboard port on three
dimensions. Full deep-dive in
[`PATIENT_DASHBOARD_MIGRATION.md`](PATIENT_DASHBOARD_MIGRATION.md);
TL;DR below.

**Why move off PHP-rendered.** Server-rendered PHP couples every
interaction to a round-trip. Optimistic UI, streaming SSE token
output, drawer state that survives tab navigation, and strong typing
across the data boundary all need a client-side state container the
PHP-rendered page can't honestly provide. The headline argument is the
**separation itself**, not the framework — moving the FHIR API from
"backing store of last resort" to "stable contract" lets any
FHIR-compliant backend swap in without re-rendering anything.

**Why Vue 3 specifically.** Honest comparisons in the deep-dive: React
brings more ceremony per component (hook-rule footguns), Angular's
RxJS+DI+decorators is over-engineered for a card grid, Svelte's
Bootstrap-aligned ecosystem is thinner, Qwik's resumability story is
sharp but the UI-library and OAuth2-client ecosystem isn't there yet
for clinical card surfaces, and HTMX returns HTML fragments — actively
contradicts "move presentation off the server." Vue's
single-file-component model mirrors OpenEMR's "one file = one surface"
pattern, Pinia is the right state-management size, native TypeScript
inference is strong, and Vite iteration speed was load-bearing on a
3.5-day clock. The defense doesn't claim Vue is *the best* framework;
it claims Vue is the **right** framework for this specific port.

**Tradeoffs accepted.** Larger runtime than Svelte/Qwik (~30 KB
gzipped), SPA not SSR (slower cold-load — acceptable because clinicians
log in once and stay), less hyped than Qwik in 2026 (we trade novelty
for shipping reliability — for clinical software predictability *is*
a feature), smaller ecosystem than React (we're not at the edges of
what Vue supports), explicit `() => import()` lazy-route splits (one-line
cost per route).

## Correctness story

This isn't a vibe demo. Three load-bearing safety properties hold the
project together:

**Bbox citation overlay.** The vision-extractor's structured-output
contract requires every scanned-source field to carry a `PageBBox`
with `bbox_confidence ≥ 0.7`. The dashboard's
`<DocumentViewer>` modal renders the source PDF with PDF.js and
overlays the bounding boxes the model emitted. A clinician
verifying an extraction clicks "View source (N)", sees the source
pixels, and decides — the equivalent of a quote-and-page-number for
a structured field on a scanned form. The alternative is "trust the
model on a chart write," which is wrong for clinical software.

**Eval gate self-test.** The W2 eval pipeline (Tasks 15–22, shipped
2026-05-08) runs a 50-case YAML golden suite distributed
**12 / 10 / 10 / 8 / 10** across `extraction` /
`evidence_retrieval` / `citations` / `refusal` / `missing_data`. The
gate's load-bearing correctness property is **proven on every CI
run**:
[`tests/eval/gate/test_gate_blocks_regression.py`](sidecar/tests/eval/gate/test_gate_blocks_regression.py)
(Task 19) constructs an adapter that strips citations off a
fabricated `A1c = 15.5%` claim, runs the full suite through it, and
asserts the gate returns `verdict.passed is False` with the expected
violation kinds. This is a safety property — "the gate would catch
this regression" — not a regression check. Headline pass/fail per
category is in
[`docs/eval-report-2026-05-08.md`](docs/eval-report-2026-05-08.md);
the pinned baseline at
[`sidecar/tests/eval/baselines/week2.json`](sidecar/tests/eval/baselines/week2.json)
is intentionally a stub (`_meta.status: "stub"`) until a measured
real-LLM regen happens.

**Sidecar-initiated persistence (Option A).** When the vision
extractor produces an `IntakeFormExtraction` or `LabPdfExtraction`,
the sidecar posts it to OpenEMR in the same turn — single
round-trip from the dashboard's perspective; the response carries a
`persisted_resource_id`. Writes route through
`QuestionnaireResponseService::saveQuestionnaireResponse()` and
`InternalLabPersistController` so the standard service events fire
and the audit trail is single-sourced. Putting the "who/what/when"
reconstruction in the client would have been the defensible-looking
choice and the wrong one — the persistence layer is also the audit
layer.

Headline cost / latency from
[`docs/w2-cost-latency-report.md`](docs/w2-cost-latency-report.md):

| Question | Answer |
| --- | --- |
| Per-turn cost — chart Q&A (no doc) | **≈ $0.011** |
| Per-turn cost — extraction turn (2-page intake) | **≈ $0.022** |
| Per-turn cost — RAG-augmented chart Q&A | **≈ $0.014** |
| p50 latency — chart Q&A (warm) | **≈ 2.5 s** |
| p95 latency — chart Q&A (warm) | **≈ 5 s** (≤ 7 s ARCHITECTURE.md ceiling) |
| p95 latency — extraction turn (warm, 2-page) | **≈ 12 s** (intentionally over-budget for upload UX) |
| 50-case W2 eval projected spend | **≈ $0.65** (worst case ≤ $1.50) |

Numbers are derived from the closed-form pricing in
[`sidecar/src/agentforge/observability/cost.py`](sidecar/src/agentforge/observability/cost.py)
applied to per-call envelopes — not yet measured against the real
agent on the droplet. The cost report is explicit about the
measured-vs-derived split.

## Local development quickstart

Three independent surfaces. Pick the one that matches what you're
working on.

**Just the dashboard, against an OpenEMR + sidecar you already have running:**

```bash
cd vue-ui
npm install
npm run dev   # Vite dev server at http://localhost:5173
              # Proxies /api/* and /auth/* to the sidecar
```

**Full stack on dev-easy** (OpenEMR + MySQL + sidecar + dashboard):

```bash
# 1. Bring up OpenEMR + MySQL stack (5–10 min on first run)
cd docker/development-easy && docker compose up --detach --wait

# 2. Run the sidecar (host-mode; needs uv + an Anthropic API key)
cd sidecar && uv sync && uv run uvicorn agentforge.main:app --reload

# 3. Seed the four demo personas (Chen / Whitaker / Reyes / Kowalski)
docker compose exec openemr php /openemr/scripts/seed-demo-patients.php

# 4. Run the dashboard dev server
cd vue-ui && npm install && npm run dev
open http://localhost:5173/   # admin / pass on a fresh dev-easy
```

**Sidecar local** (LangGraph orchestrator + BFF, no dashboard):

```bash
cd sidecar && uv sync && uv run uvicorn agentforge.main:app --reload
```

Set `ANTHROPIC_API_KEY` in `sidecar/.env`; see
[`sidecar/.env.example`](sidecar/.env.example) for the full env
surface (JWT secret, Redis URL, OAuth client config, etc.).

For the production deploy path (DigitalOcean droplet, OAuth client
registration, Apache reverse proxy, sidecar container with
`--env-file`), see [`scripts/deploy-droplet.sh`](scripts/deploy-droplet.sh)
and [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

For full developer setup (PHPUnit, Vitest, PHPStan level 10, prek
hooks, eval suite), see [`CLAUDE.md`](CLAUDE.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Testing

Three suites, three commands:

```bash
# Sidecar (Python — FastAPI, LangGraph, vision, eval, persist)
cd sidecar && uv run pytest

# PHP module (host, no Docker required)
composer phpunit-isolated

# Dashboard (Vue + Vitest, JSDOM)
cd vue-ui && npm test
```

Current numbers (as of 2026-05-09):

| Suite | Result |
| --- | --- |
| Sidecar (`uv run pytest`) | **1335 passed, 18 deselected** |
| PHP module (`composer phpunit-isolated`) | **384 / 384 passing** |
| PHPStan level 10 | **clean** |

The eval gate runs on GitLab CI and GitHub Actions via
`sidecar/scripts/run_eval_gate.sh`; today it runs against the stub
baseline (no-regression check), with the gate self-test (Task 19)
carrying the load-bearing correctness assertion.

## Status / roadmap

**Shipped** (W1 + W2):

| Surface | Status |
| --- | --- |
| W1 chart-Q&A loop (8 typed tools, parallel dispatch, verifier) | done |
| W1 streaming verifier with `[type #id]` citations | done |
| W1 self-hosted Langfuse, HMAC-pseudonymized IDs | done |
| W2 doc upload + bbox citation overlay | done |
| W2 multi-format extraction (intake form + lab PDF schemas) | done (lab graph worker pending end-to-end wiring) |
| W2 hybrid RAG (BM25 + dense + RRF + cross-encoder) over 30-doc guideline corpus | done |
| W2 LangGraph supervisor with planner / extractor / retriever / synthesizer / verifier nodes | done |
| W2 eval gate (50 cases, programmatic + LLM judge, regression-blocking) | done |
| W2 patient dashboard port to Vue 3 (header + 5 cards + drawer + bonus) | done |
| W2 OAuth2 / OIDC via BFF (token never touches JS) | done |
| W2 sidecar-initiated persistence (single round-trip + audit trail) | done |
| Production deploy (DigitalOcean droplet, Apache reverse proxy) | done |

**Cards shipped on the dashboard** (per
`PATIENT_DASHBOARD_MIGRATION.md` Status table):

| Card | Status | Notes |
| --- | --- | --- |
| Patient Header | done | T38.3 |
| Allergies | done | T38.4 |
| Problem List | done | T38.5 |
| Medications | done | T38.6 — `MedicationRequest` filtered by status |
| Recent Encounters | done | T38.10 ride-along |
| Lab Results (bonus) | done | T38.9 — sparkline + out-of-range coloring |
| Vitals Strip (bonus) | done | T38.9 ride-along |
| Prescriptions | **deferred** | subsumed by Medications card filtering `MedicationRequest` by status; no separate surface |
| Care Team | **deferred** | Synthea-imported personas have empty CareTeam tables — no data to render |

**Not shipped** (per `PATIENT_DASHBOARD_MIGRATION.md` "Future work"):

- DocumentViewer is PDF-only (PNG intake forms render through the
  extraction pipeline but not the View-source modal).
- Bbox placement is approximate (Haiku-vision lands the right region;
  pixel precision would need a larger model or a tessera-OCR alignment
  pass).
- Demographics labels render as raw `snake_case` in the
  `<ExtractionPanel>`.
- The chat reply duplicates panel content (synthesizer narrates
  what the panel already shows).
- Planner Haiku tool-call fallback warning fires on some turns;
  orchestrator falls back to `default_plan_for(use_case)`.
- Eval baseline is a stub — gate self-test (Task 19) carries the
  correctness claim; absolute pass-rate measurement is the follow-up
  (cost-projected at $0.65 per measured run).
- Lab graph worker not yet wired end-to-end (`P1.2` adds the
  doc_type dispatch; `P1.1`'s persister is `isinstance`-typed and
  forward-compat-ready).
- Sign & Finalize / Edit demographics are preview-only (need
  `POST /api/fhir/Encounter` and `PATCH /Patient`).
- Token refresh not implemented — access_token expires ~1h, SPA
  bounces to /login.
- Demo guideline corpus is project-prepared summaries, framed as
  "demo stub only" in `sidecar/data/guidelines/NOTICE.md`.
- No SFC-level integration tests for the drawer (composable + store
  + pure-helper unit coverage is in).

## Submission artifacts

| Artifact | What it shows |
| --- | --- |
| [`docs/eval-report-2026-05-08.md`](docs/eval-report-2026-05-08.md) | W2 eval framework + gate proof of correctness; 50-case suite distribution |
| [`docs/w2-cost-latency-report.md`](docs/w2-cost-latency-report.md) | Per-turn cost & latency by node, 50-case eval projection, cliffs and mitigations |
| [`docs/w2-demo-script.md`](docs/w2-demo-script.md) | Recording script + shot list + pre/post-record runbook for the W2 demo video |
| [`docs/w2-defense-slides.html`](docs/w2-defense-slides.html) | Defense slide deck — three-leg architectural defense, correctness story, cost discipline |
| [`PATIENT_DASHBOARD_MIGRATION.md`](PATIENT_DASHBOARD_MIGRATION.md) | T38.13 — full dashboard port defense doc (this is the single deepest read) |

## Pointers

| File | Scope |
| --- | --- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | W1 system design — six load-bearing decisions, three explicit tradeoffs |
| [`W2_ARCHITECTURE.md`](W2_ARCHITECTURE.md) | W2 multimodal evidence agent — intake contract, verifier floor |
| [`W2_DEFENSE.md`](W2_DEFENSE.md) | W2 architecture summary and defense |
| [`PATIENT_DASHBOARD_MIGRATION.md`](PATIENT_DASHBOARD_MIGRATION.md) | Why Vue 3 (vs React/Angular/Svelte/Qwik/HTMX), tradeoffs, full shipped state |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Production deploy path — droplet, OAuth client, Apache proxy, env vars |
| [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md) | Running log of decisions that diverged from the planning artifacts, with rationale |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records — auth bridging, etc. |
| [`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md) | Resume context: deployed state, droplet config, demo runbook, known gaps |
| [`USERS.md`](USERS.md) | Personas, use cases, success metrics |
| [`AUDIT.md`](AUDIT.md) | Audit-trail surfaces and PHI policy |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Upstream OpenEMR contribution guide (preserved verbatim) |
| [`CLAUDE.md`](CLAUDE.md) | Repo conventions: PSR standards, type system, error handling, PHPStan policy |

## What's in the repository

```
vue-ui/                                 # Vue 3 patient dashboard SPA + AgentForge drawer
sidecar/                                # Python sidecar: orchestrator, tools, vision extractor, BFF, eval
interface/modules/custom_modules/
  oe-module-agentforge/                 # PHP module: JWT mint, internal endpoints, record fetchers
prompts/                                # Versioned prompt library (synthesizer, planner, intake/lab vision)
docker/agent/                           # Sidecar dev compose stack
docker/development-easy/                # OpenEMR dev stack (upstream, augmented)
docker/openemr-proxy/                   # Apache reverse-proxy config for the production cutover
scripts/                                # Deploy + demo seed scripts (deploy-droplet.sh, seed-demo-patients.php)
docs/                                   # ADRs, deployment notes, eval reports, deviations
.taskmaster/                            # TaskMaster roadmap (week1-gaps, week2, master tags)
week2/example-documents/                # Fabricated W2 corpus (intake forms, lab results, referrals, HL7)
```

## About this fork

This repository is a fork of [openemr/openemr](https://github.com/openemr/openemr).
The upstream OpenEMR README — describing OpenEMR itself, its
community, support resources, and acknowledgments — is preserved
verbatim at [`README.upstream.md`](README.upstream.md). Upstream
documentation (`API_README.md`, `FHIR_README.md`, `DOCKER_README.md`,
`CONTRIBUTING.md`, etc.) is unchanged from origin.

The AgentForge additions are licensed under the same
[GPL-3.0](LICENSE) as upstream OpenEMR.
