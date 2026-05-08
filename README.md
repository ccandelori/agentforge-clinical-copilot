# AgentForge — Clinical Co-Pilot for OpenEMR

A chart-aware AI agent embedded in [OpenEMR](https://open-emr.org) with a
modern Vue 3 patient dashboard and a multimodal evidence pipeline that
turns scanned intake forms into structured chart data — with every
extracted field grounded to a pixel-coordinate citation on the source
PDF.

Built across two waves:

- **Week 1 — Chart Q&A.** Verify-before-emit synthesis over the patient's
  chart (problems, meds, labs, vitals, allergies, immunizations,
  procedures, notes). Every assistant claim traces back to a specific
  record; out-of-context launches are refused; cross-patient leakage is
  caught before it reaches the screen.
- **Week 2 — Multimodal evidence + dashboard rewrite.** Upload a PDF
  intake form or lab result; the orchestrator renders pages, runs a
  vision extractor, enforces a bbox-confidence floor (≥ 0.7) on every
  scanned-source citation, and surfaces the structured extraction inline
  in a "View source" overlay back over the original document. The legacy
  PHP-rendered patient summary is replaced by a Vue 3 SPA served from
  the sidecar at the same origin.

## Live demo

| URL | Notes |
| --- | --- |
| [`https://143.244.157.90:9300/dashboard/`](https://143.244.157.90:9300/dashboard/) | Vue 3 patient dashboard with embedded AgentForge drawer — **self-signed cert**, browser will warn (click through) |

Login: `admin` / `pass`.

**Try the W2 intake-extraction loop:**

1. Pick **Margaret Chen** (`MRN-2026-04481`) from the patient list.
2. Open the AgentForge drawer (right edge), click the paperclip in the
   composer, attach `week2/example-documents/intake-forms/p01-chen-intake-typed.pdf`.
3. Send "Extract this intake form."
4. The chat reply summarises the form; the `<ExtractionPanel>` below
   renders the structured fields (chief concern, demographics,
   medications, allergies, family history, unsupported_fields).
5. Click **"View source (N)"** — a modal opens with the PDF rendered and
   blue rectangles overlaid where each field came from.

**Try the W1 chart-Q&A loop** on the same patient:

- *"Give me a chart overview."*
- *"Is it safe to start her on ibuprofen?"*
- *"What's changed in the last 90 days?"*

Three other personas (Whitaker / Reyes / Kowalski) are also seeded for
the additional intake forms in `week2/example-documents/intake-forms/`.

## Architecture in five lines

1. **Vue 3 SPA** (`vue-ui/`) is the patient dashboard. Auth via OAuth2
   against OpenEMR's authorization server, brokered by a BFF cookie
   issued at the dashboard origin; FHIR data fetches and agent turns
   ride that cookie. The AgentForge drawer is a top-level slide-out.
2. **Python sidecar** (`sidecar/`, FastAPI + LangGraph) terminates BFF
   auth, mints per-turn JWTs scoped to the open patient, runs the
   orchestrator, fans out to a typed tool catalog in parallel, and
   streams the synthesized response.
3. **Multimodal extraction.** When a turn carries `document_id`, the
   orchestrator fetches bytes via a JWT-authed PHP internal endpoint
   (`internal/get_document_bytes.php`), renders pages with PyMuPDF, and
   routes through a forced-tool-call vision extractor whose output
   schema requires every scanned-source citation to carry a
   `PageBBox` with `bbox_confidence ≥ 0.7`.
4. **Verify-before-emit.** Every assistant claim is gated against the
   per-turn citation cache; ungrounded claims are replaced with a
   refusal marker. Citations carry `[type #id]` markers parseable back
   to a row in OpenEMR.
5. **Operational glue.** Session memory in Redis; Langfuse traces
   carrying HMAC-pseudonymised IDs (no PHI); per-turn cost on the
   `X-Agent-Cost-USD` response header; sensitivity policy gates record
   visibility before tool dispatch.

Read deeper:

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — W1 system design, six load-bearing decisions, three explicit tradeoffs.
- [`W2_ARCHITECTURE.md`](W2_ARCHITECTURE.md) — W2 multimodal evidence agent, intake contract, verifier floor.
- [`W2_DEFENSE.md`](W2_DEFENSE.md) — W2 architecture summary and defense.
- [`PATIENT_DASHBOARD_MIGRATION.md`](PATIENT_DASHBOARD_MIGRATION.md) — Why Vue 3 (vs React / Angular / Svelte / Qwik / HTMX), tradeoffs accepted.
- [`docs/DEVIATIONS.md`](docs/DEVIATIONS.md) — Running log of decisions that diverged from the planning artifacts, with rationale.
- [`docs/adr/`](docs/adr/) — Architecture Decision Records (auth bridging, etc.).
- [`USERS.md`](USERS.md) — Personas, use cases, success metrics.

## 60-second local quickstart

```bash
# 1. Bring up OpenEMR + MySQL stack (5-10 min on first run)
cd docker/development-easy && docker compose up --detach --wait

# 2. Run the sidecar (host-mode; needs uv + an Anthropic API key)
cd sidecar && uv sync && uv run uvicorn agentforge.main:app --reload

# 3. Run the dashboard dev server (Vite proxies /api/* and /auth/* to the sidecar)
cd vue-ui && npm install && npm run dev

# 4. Open the SPA
open http://localhost:5173/      # admin / pass
```

The AgentForge drawer is the right-edge slide-out. Set
`ANTHROPIC_API_KEY` in `sidecar/.env`; see
[`sidecar/.env.example`](sidecar/.env.example) for the full env surface
(JWT secret, Redis URL, OAuth client config, etc.).

For the production deploy path (DigitalOcean droplet, OAuth client
registration, Apache reverse proxy, sidecar container with
`--env-file`), see [`scripts/deploy-droplet.sh`](scripts/deploy-droplet.sh)
and the operational notes in [`docs/NEXT-SESSION.md`](docs/NEXT-SESSION.md).

For full developer setup (PHPUnit, Vitest, PHPStan level 10, prek
hooks, eval suite), see [`CLAUDE.md`](CLAUDE.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## What's in the repository

```
vue-ui/                                 # Vue 3 patient dashboard SPA + AgentForge drawer
sidecar/                                # Python sidecar: orchestrator, tools, vision extractor, BFF auth
interface/modules/custom_modules/
  oe-module-agentforge/                 # PHP module: JWT mint, internal endpoints (doc bytes, doc upload), record fetchers
prompts/                                # Versioned prompt library (synthesizer, planner, intake/lab vision)
docker/agent/                           # Sidecar dev compose stack
docker/development-easy/                # OpenEMR dev stack (upstream, augmented)
docker/openemr-proxy/                   # Apache reverse-proxy config for the production cutover
scripts/                                # Deploy + demo seed scripts (deploy-droplet.sh, seed-demo-patients.php)
docs/                                   # ADRs, deployment notes, eval reports, deviations
.taskmaster/                            # TaskMaster roadmap (week1, week2, etc.)
week2/example-documents/                # Fabricated W2 corpus (intake forms, lab results, referrals, HL7)
```

## About this fork

This repository is a fork of [openemr/openemr](https://github.com/openemr/openemr).
The upstream OpenEMR README — describing OpenEMR itself, its community,
support resources, and acknowledgments — is preserved verbatim at
[`README.upstream.md`](README.upstream.md). All upstream documentation
(`API_README.md`, `FHIR_README.md`, `DOCKER_README.md`, `CONTRIBUTING.md`,
etc.) is unchanged from origin.

The AgentForge additions are licensed under the same
[GPL-3.0](LICENSE) as upstream OpenEMR.
