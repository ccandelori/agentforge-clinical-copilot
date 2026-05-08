# AgentForge — Clinical Co-Pilot for OpenEMR

A chart-aware AI agent embedded in [OpenEMR](https://open-emr.org) as a
custom module, with orchestration and LLM calls delegated to a Python
sidecar. Built for the user story in [USERS.md](USERS.md): a hospitalist
needs to walk into a 2 AM admit knowing what matters about a patient she
has never met, without reading 14 months of notes herself. Every claim
the agent surfaces traces back to a specific record in this patient's
chart; out-of-context launches are refused; cross-patient references
are caught before they reach the screen.

## Live demo

| URL | Notes |
| --- | --- |
| [`https://143.244.157.90:9300/dashboard`](https://143.244.157.90:9300/) | OpenEMR over HTTPS — **self-signed cert**, browser will warn (click through) |

Login: `admin` / `pass`. Recommended demo patient: **Eula Crist** (a
complex chronic patient with CKD stage 3, hypertension, hyperlipidemia,
multiple medications, and recent labs). Open her chart, find the
"AgentForge" panel in the patient summary, and ask:

- *"Give me a chart overview."* — exercises the admit-synthesis path.
- *"Is it safe to start her on ibuprofen?"* — exercises the
  contraindication path against her CKD.
- *"What's changed in the last 90 days?"* — exercises the delta path.

## Architecture in five lines

1. **OpenEMR custom module** (`interface/modules/custom_modules/oe-module-agentforge/`) renders the chat panel inside the patient summary and mints a per-turn JWT scoped to the open patient.
2. **Python sidecar** (`sidecar/`, FastAPI + LangGraph) receives the JWT, fans out to a typed tool catalog (problems, medications, labs, vitals, allergies, immunizations, procedures, notes, search, encounters, demographics) in parallel, then streams the synthesized response back over SSE.
3. **Verify-before-emit**: every assistant sentence is gated against the per-turn citation cache before reaching the wire — ungrounded claims are replaced with a refusal marker, never streamed and rewritten. Citations carry `[type #id]` markers parseable back to a row in OpenEMR.
4. **Session memory** (Redis) supports multi-turn conversation; **Langfuse** captures non-PHI traces with HMAC-pseudonymized IDs; **sensitivity policy** gates record visibility before tool dispatch; **per-turn cost** rides back on the `X-Agent-Cost-USD` response header.
5. **Eval suite** runs the real orchestrator against twelve YAML failure-mode cases (happy-path, missing-data, ambiguous, unauthorized, hallucination) with deterministic + LLM-judge graders — see the most recent [`docs/eval-report-2026-05-03.md`](docs/eval-report-2026-05-03.md).

Read deeper:

- [`AUDIT.md`](AUDIT.md) — OpenEMR codebase audit and the gaps this project closes.
- [`USERS.md`](USERS.md) — User research, persona (Dr. Aisha Patel), use cases, success metrics.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — Six load-bearing decisions, three explicit tradeoffs, full system design.
- [`DEPLOY.md`](DEPLOY.md) — Pre-deploy gates, deploy steps, rollback plan, post-deploy validation.
- [`docs/eval-summary-2026-05-03.md`](docs/eval-summary-2026-05-03.md) — Eval narrative across baseline (6/7 live LLM pass), YAML failure-mode cases, and live demo session.
- [`docs/cost-analysis-2026-05-03.md`](docs/cost-analysis-2026-05-03.md) — Per-turn cost (~$0.03), daily/monthly projections, ROI sketch.

## 60-second local quickstart

```bash
# 1. Clone and bring up OpenEMR + MySQL stack (5-10 min on first run)
git clone <this-repo> openemr && cd openemr
cd docker/development-easy && docker compose up --detach --wait

# 2. Bring up the sidecar
cd ../agent && docker compose up --build --detach

# 3. Open the app, log in, pick a patient
open http://localhost:8300/      # admin / pass
```

The AgentForge panel appears in the patient summary view. See
[`docker/agent/README.md`](docker/agent/README.md) for the
host-script alternative (`./sidecar/scripts/sidecar.sh start`),
environment variables, and Anthropic API key configuration.

For full developer setup including PHPUnit, Jest, PHPStan, prek
hooks, and the eval suite, see [`CLAUDE.md`](CLAUDE.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## What's in the repository

```
interface/modules/custom_modules/
  oe-module-agentforge/      # PHP module: panel, JWT mint, proxy controller
sidecar/                     # Python sidecar: orchestrator, tools, verifier, eval
prompts/                     # Versioned prompt library (synthesizer, planner)
docker/agent/                # Sidecar dev compose stack
docker/development-easy/     # OpenEMR dev stack (upstream)
docs/                        # ADRs, deployment notes, eval reports, deviations
.taskmaster/                 # TaskMaster roadmap (master + week1-gaps tags)
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
