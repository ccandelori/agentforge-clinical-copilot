# Where we left off — 2026-05-08 evening (eval pipeline shipped, ~30 hours to deadline)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

Two threads are now shipped end-to-end, both pushed to both remotes
(`labs.gauntletai.com/cameroncandelori/openemr` and the public mirror
at `github.com/ccandelori/agentforge-clinical-copilot`):

1. **W2 doc-upload + citation overlay** (shipped morning of 2026-05-08).
   Live on the droplet at
   [https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/).
2. **W2 eval pipeline** (shipped this evening). 11 tasks merged in two
   parallel-agent waves: hybrid-RAG-backed evidence-retriever node,
   50-case YAML suite, programmatic + LLM-as-judge graders, eval gate
   with baseline + thresholds, gate self-test, GitLab CI agent-eval
   job, GitHub Actions mirror, eval-smoke pre-commit hook, observability
   extensions, the W2 evaluation report, and the production
   SupervisorAdapter that closes the loop. Sidecar suite: **1313 passed,
   30 deselected** (gate_validation + eval_smoke marker tags).

W2 deadline: Sun 2026-05-10 noon. **~30 hours left.**

**Open priorities** (in rough order):

1. **T38.13 — `PATIENT_DASHBOARD_MIGRATION.md` defense doc.** Graded
   artifact for the W2 surprise challenge. Some content is already in
   `PATIENT_DASHBOARD_MIGRATION.md` at the repo root; needs a refresh
   against current shipped state.
2. **Defense slides refresh** (`docs/w2-defense-slides.html`). Still
   has the pre-session edits sitting in stash (`presession-slides-WIP`).
   Pop, then update for the doc-upload pipeline + bbox overlay
   trust-artifact story AND the eval-pipeline-as-correctness-claim story.
3. **Manual baseline regen** so the eval gate has a measured baseline,
   not a stub. See "Eval pipeline state" below.
4. **Live-demo dry run.** End-to-end on Chen + Whitaker (typed PDFs);
   time it, note flakes. The chat-reply-duplicates-panel and
   snake_case demographics labels are the visible papercuts.
5. **Operational deferreds** for the new CI surface (`GLAB_TOKEN`,
   GitHub branch protection). See "CI / operational deferreds" below.

## Eval pipeline state

The infrastructure is shipped and proven correct (the gate self-test
catches a deliberately-regressed adapter); what's NOT yet measured is
how the agent itself scores on the 50 W2 cases.

**The stub.** `sidecar/tests/eval/baselines/week2.json` is structurally
pinned at 1.0 across all five categories with `_meta.status: "stub"`.
The gate compares against this stub today, which is fine for catching
regressions from this baseline forward but doesn't tell you the absolute
pass rate.

**The bridge.** `sidecar/src/agentforge/eval/supervisor_adapter.py`
is the production `Callable[[EvalCase], SupervisorOutput]` adapter that
drives `build_graph().ainvoke()` and shapes the result for the eval
runner. It ships fully tested with mocked LLMs.

**The regen CLI.**

```bash
cd sidecar
uv run python -m agentforge.eval.regenerate_baseline \
    --output tests/eval/baselines/week2.json
# or for a token-free smoke:
uv run python -m agentforge.eval.regenerate_baseline --mock --output /tmp/smoke.json
```

The real-LLM branch raises `NotImplementedError` until a human edits
`_build_real_supervisor_and_harness` in
`sidecar/src/agentforge/eval/regenerate_baseline.py` to construct the
deps tree their run needs (settings, redis client, openemr client, etc.).
This is a deliberate seam — the module-level imports stay light so the
CLI's mock path doesn't drag FastAPI/Redis/OpenEMR-client startup costs.

**The cost.** A measured run is 50 cases × (vision extract + RAG +
synthesizer + judge) = several dollars at current rates. Project the
total via `sidecar/src/agentforge/observability/cost.py` first.

**Judge routing limitation** (logged in DEVIATIONS): the LLM judge's
`factually_consistent` category fires only for `HALLUCINATION` /
`REFUSAL` `EvalCategory` values. The W2 case suite uses
`extraction` / `evidence_retrieval` / `citations` / `refusal` /
`missing_data`. The gate self-test catches *citation-strip-shaped*
fabrications via the programmatic `citation_present` grader; extending
the judge routing for value-fabrication coverage is a documented
follow-up (not blocking the W2 deadline).

## CI / operational deferreds

The eval gate runs on both GitLab CI (Task 20) and GitHub Actions
(Task 22) via the shared `sidecar/scripts/run_eval_gate.sh`. CI today
runs with a mocked supervisor, so it's purely a regression check
against the stub baseline. To make CI useful as a *correctness* check
rather than a *no-regression* check:

1. **Set `GLAB_TOKEN`** as a masked CI variable on the GitLab project
   so MR comments actually post (Task 20). Without it, the gate still
   passes/fails correctly but the diff report only ships as a job
   artifact.
2. **Configure GitHub branch protection** on the public mirror's `main`
   to require the `agent-eval` workflow status check (Task 22).
3. **Publish the pre-baked sidecar Docker image** to a registry so the
   future real-LLM manual eval job can pull it. The deploy script
   currently builds it locally; nothing pushes to a registry yet.
4. **Push the GitHub mirror after every GitLab push** (or set up a
   mirror push). Currently both remotes are at the same SHA after
   manual `git push origin main && git push github main`.

## Demo runbook (production droplet)

1. Open [https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/) — accept self-signed cert.
2. Login (credentials in repo `README.md`).
3. Pick **Margaret Chen** (`MRN-2026-04481`) — typed PDF, cleanest extraction.
4. Open AgentForge drawer (right edge).
5. Click paperclip → attach `week2/example-documents/intake-forms/p01-chen-intake-typed.pdf`.
6. Send "Extract this intake form."
7. Wait ~12-15s (Haiku vision; cold first call). Chat reply lists extracted fields; `<ExtractionPanel>` renders below bubble.
8. Click **"View source (18)"** — modal opens, PDF renders, blue rectangles overlay extracted-field regions.

Three other personas seeded for additional test runs:

| Persona | pid | UUID | Intake form |
|---|---|---|---|
| Margaret Chen | 29 | `a1b9f2f6-d2eb-49e1-adce-35ca6c1f8ac0` | `p01-chen-intake-typed.pdf` |
| James Whitaker | 30 | `a1b9f2f6-ed02-4156-9dcb-95d77123f009` | `p02-whitaker-intake.pdf` |
| Sofia Reyes | 31 | `a1b9f2f6-faae-4a57-a54a-9a4c6af7611e` | `p03-reyes-intake.png` |
| Robert Kowalski | 32 | `a1b9f2f7-0edb-4526-9656-a14c16c90823` | `p04-kowalski-intake.png` |

Re-run `scripts/seed-demo-patients.php` (idempotent on `pubpid`) if any persona disappears.

PNG personas (Reyes, Kowalski) won't render in the View-source modal —
PDF.js can't parse `image/png` bytes. **Stick to typed PDFs (Chen,
Whitaker) for the demo's bbox-overlay story.**

## Production droplet — config knobs

### Live URL
[https://143.244.157.90:9300/dashboard/](https://143.244.157.90:9300/dashboard/) — vue-ui SPA. Self-signed cert.

### Apache reverse proxy

The droplet's OpenEMR container hosts both PHP and the Vue SPA at the
same origin so the HttpOnly session cookie rides the OAuth flow:

```
Browser → https://143.244.157.90:9300/...
            │
            ├─ /dashboard/*  ──► Apache → agentforge-sidecar:8000/*  (StaticFiles, prefix-stripped)
            ├─ /auth/*       ──► Apache → agentforge-sidecar:8000/auth/*
            ├─ /api/*        ──► Apache → agentforge-sidecar:8000/api/*
            └─ /apis/, /interface/, /portal/, /oauth2/*  ──► OpenEMR PHP (unchanged)
```

The proxy config is in `docker/openemr-proxy/agentforge-proxy.conf`,
deployed to `/etc/apache2/conf.d/zz-agentforge-proxy.conf` inside the
openemr container. **It's not persistent across container recreation**
— if `development-easy-openemr-1` ever restarts from scratch, re-inject:

```bash
scp docker/openemr-proxy/agentforge-proxy.conf root@<droplet>:/opt/agentforge/agentforge-proxy.conf
ssh root@<droplet> \
  'docker cp /opt/agentforge/agentforge-proxy.conf development-easy-openemr-1:/etc/apache2/conf.d/zz-agentforge-proxy.conf && \
   docker exec development-easy-openemr-1 httpd -k graceful'
```

### Production OAuth client

Registered in OpenEMR `oauth_clients`. The original client_id leaked
on GitHub during the public-mirror push and has been rotated; old
client is `is_enabled=0, revoke_date=NOW()`.

- `client_id` / `client_secret`: live values are in
  `/opt/agentforge/sidecar/.env` on the droplet — never check them
  into the repo.
- `redirect_uris`: `["https://143.244.157.90:9300/auth/callback"]`
- `post_logout_redirect_uris`: `["https://143.244.157.90:9300/dashboard/"]`
- `is_enabled = 1`

Sidecar env in `/opt/agentforge/sidecar/.env` on droplet (sensitive
values redacted here):
```
DASHBOARD_APP_URL=https://143.244.157.90:9300
DASHBOARD_OAUTH_AUTHORITY=https://143.244.157.90:9300/oauth2/default
DASHBOARD_OAUTH_CLIENT_ID=<live; rotated 2026-05-08>
DASHBOARD_OAUTH_CLIENT_SECRET=<live; rotated 2026-05-08>
DASHBOARD_OAUTH_REDIRECT_URI=https://143.244.157.90:9300/auth/callback
DASHBOARD_OAUTH_POST_LOGOUT_REDIRECT_URI=https://143.244.157.90:9300/dashboard/
DASHBOARD_SESSION_COOKIE_SECURE=true
DASHBOARD_FHIR_BASE_URL=http://openemr/apis/default/fhir
CLAUDE_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_VISION_MODEL=claude-haiku-4-5-20251001
```

**`docker restart` does NOT reload env-file vars** — to push env-file
changes you have to `docker rm -f` and `docker run` again with
`--env-file`. The deploy script's `deploy_sidecar` does this correctly;
only manual `docker restart` invocations are dangerous.

### OpenEMR admin password

Rotated from default `admin/pass` to a 24-char generated value (live
README has it). Don't roll back to default — random brute-force
scanners hit the old creds constantly.

### Local dev pattern for personal config

`scripts/.env.local` (gitignored) holds your `DROPLET_HOST` and any
other personal overrides. The deploy script auto-sources it. See
`scripts/.env.local.example` for the template. Required because
`./scripts/deploy-droplet.sh` no longer carries a hardcoded default.

### Per-droplet Synthea data backfill (vitals)

Each droplet's MySQL is a separate database. The dev-easy fix for
Synthea-imported vitals (forms-row backfill + uuid_mapping backfill)
has to be repeated per environment:

```bash
# 1. Link orphan form_vitals to encounters
ssh root@<droplet> 'docker exec development-easy-mysql-1 mariadb -uopenemr -popenemr openemr -e "
INSERT INTO forms (date, encounter, form_name, form_id, pid, user, deleted, formdir, issue_id, provider_id)
SELECT v.date,
  (SELECT fe.encounter FROM form_encounter fe WHERE fe.pid = v.pid ORDER BY ABS(TIMESTAMPDIFF(SECOND, fe.date, v.date)) LIMIT 1),
  \"Vitals\", v.id, v.pid, \"ExternalProvider\", 0, \"vitals\", 0, 0
FROM form_vitals v
WHERE NOT EXISTS (SELECT 1 FROM forms f WHERE f.form_id = v.id AND f.formdir=\"vitals\");"'

# 2. Backfill uuid_mappings (idempotent; order matters — forms first)
ssh root@<droplet> 'docker exec development-easy-openemr-1 php -r "
\$_GET[\"site\"] = \"default\";
\$ignoreAuth = true;
require \"/var/www/localhost/htdocs/openemr/interface/globals.php\";
echo \OpenEMR\Common\Uuid\UuidMapping::createAllMissingResourceUuids();"'
```

### `DASHBOARD_FHIR_BASE_URL` must be set in sidecar env

Default is empty → all FHIR requests fail with `502 Bad Gateway`
("FHIR upstream unreachable"). Production value:
`http://openemr/apis/default/fhir` (docker network alias).

### OpenEMR's `site_addr_oath` global must point at the droplet

Dev-easy ships with `globals.site_addr_oath = https://localhost:9300`.
OpenEMR uses this to build redirect URLs during the OAuth login bounce;
left at localhost, the user gets redirected to their own machine
mid-flow. Set per-droplet (`<droplet>` → your IP/hostname).

## How to redeploy

```bash
# Code changes in vue-ui/* or sidecar/*?
./scripts/deploy-droplet.sh sidecar    # rebuilds image, recreates container with --env-file
./scripts/deploy-droplet.sh dashboard  # rebuilds vue-ui SPA, rsyncs dist (no container restart)
./scripts/deploy-droplet.sh module     # rsyncs PHP module, docker cp into openemr container

# Sanity check
./scripts/deploy-droplet.sh check
```

If `DROPLET_HOST` is unset, the script exits 2 with a hint pointing
at `scripts/.env.local`.

## Known gaps (carried forward + new)

W2-deadline-relevant (worth deciding before Sunday noon):

1. **DocumentViewer is PDF-only.** PNG intake forms (Reyes, Kowalski)
   won't render in the modal. Demo workaround: stick to typed PDFs.
2. **Bbox placement is approximate.** Haiku-vision bboxes land in the
   right region but offset by a row/cell. Acceptable trust artifact;
   not pixel-tight.
3. **Demographics labels are raw snake_case.** Quarter-day fix: a
   `humanizeFieldName()` helper in `<ExtractionPanel>`.
4. **Chat reply duplicates panel content.** Either tighten the
   synthesizer prompt to defer to the panel, or accept it.
5. **Planner Haiku tool-call fallback warning.** Logs show
   `planner LLM returned no submit_plan tool call; falling back`.
   Orchestrator falls back to `default_plan_for(use_case)`; requests
   complete with less-tailored tool selection. ~half-day fix: separate
   `PLANNER_MODEL` env knob, pin planner to Sonnet.
6. **Eval baseline is a stub** (see "Eval pipeline state" above).
7. **Judge routing limitation** — LLM judge only fires for
   HALLUCINATION/REFUSAL categories; W2 cases use five different ones.
   Programmatic `citation_present` grader carries the load-bearing
   assertion in the gate self-test.

Post-deadline / future:

8. **No vue-ui tests at the SFC integration level.** Unit tests for
   composables and pure helpers are in. Drawer-flow integration
   tests are not.
9. **Sign & Finalize / Edit demographics are preview-only.** Same gap
   as W1 — needs `POST /api/fhir/Encounter` and `PATCH /Patient`.
   Tracked in DEVIATIONS.md.
10. **Token refresh not implemented.** OAuth access_token expires
    ~1 hr; FHIR returns 401; SPA bounces to /login.
11. **CalendarView / SettingsView are mocked** — not wired to real FHIR.
12. **Apache proxy conf is not persisted** across openemr container
    recreation — recipe above to re-inject.
13. **bge-reranker-base image cost.** Pre-baked at fp32 = ~1.1 GB
    (not the spec's ~280 MB — that was a parameter-count-as-MB
    confusion). Mitigations available (fp16 / smaller cross-encoder /
    int8-quantized variant) but not blocking. Logged in DEVIATIONS.

## Things to focus on next session

In priority order with ~30 hours left:

1. **T38.13 — `PATIENT_DASHBOARD_MIGRATION.md` defense doc.** Graded
   W2-surprise artifact. Refresh the existing doc against
   currently-shipped state. (Gates Task 38 finishing in Taskmaster.)
2. **Defense slides refresh** (`docs/w2-defense-slides.html`). Pop
   `stash@{0} presession-slides-WIP` first. Land doc-upload + bbox +
   eval-pipeline trust-artifact story.
3. **Live-demo dry run** on Chen + Whitaker. Time it, note flakes,
   decide papercut fixes (snake_case labels, chat-duplicates-panel).
4. **Decision pass on the gaps above (1–7).** Most are noted but
   undecided. Quick fixes vs. defense narrative — judgment call.
5. (Optional) **Manual baseline regen.** Edit
   `_build_real_supervisor_and_harness` in
   `sidecar/src/agentforge/eval/regenerate_baseline.py`, run on droplet,
   commit measured `baselines/week2.json`. ~2-4 hours including cost
   review. The defense narrative is stronger with measured numbers
   than with stub-pinned ones, but the gate self-test (Task 19) carries
   the correctness claim either way.
6. (Optional) **Operational deferreds.** `GLAB_TOKEN` masked variable,
   GitHub branch protection. ~30 min total.

## Branch state at this commit

- Branch: `main` (clean)
- HEAD: `50c867e67`
- Origin: GitLab (`labs.gauntletai.com/cameroncandelori/openemr`) — synced
- Mirror: GitHub (`github.com/ccandelori/agentforge-clinical-copilot`) — synced
- Sidecar suite: 1313 passed, 30 deselected (gate_validation + eval_smoke)

## Taskmaster state (`week2` tag)

- 33 done · 1 cancelled (#25 — per-chart citation overlay obsoleted by
  drawer placement decision) · 1 in-progress (#38 dashboard port
  pending T38.13 defense doc) · 5 pending
- Pending: #30 (deploy + run baseline on droplet), #32 (cost/latency
  report), #33 (demo video), #34 (replace README), #39 (CareTeam seed)
- All four (#32/#33/#34) gate on #30. #39 gates on #38.

## Stashes

- `stash@{0} presession-slides-WIP` — pre-session edits to
  `docs/w2-defense-slides.html`. Pop before the slides refresh in
  priority 2.

## What shipped this session — summary index

For full detail see `docs/DEVIATIONS.md` (every entry from 2026-05-08)
and `git log --since=2026-05-08`.

**Morning round** — W2 doc-upload thread (MR !40, ~22 commits): the
upload composable, BFF route, JWT-authed PHP upload endpoint, document
viewer with bbox overlay, ExtractionPanel, "View source" modal, OAuth
client rotation, droplet env hygiene.

**Evening round** — W2 eval pipeline (~95 commits, 14 tasks):

| Task | What |
|---|---|
| #15 | Evidence Retriever LangGraph node |
| #16 | 50-case YAML W2 eval suite (12/10/10/8/10) |
| #17 | LLM-as-Judge layer (factually_consistent + safe_refusal) + programmatic graders |
| #18 | Eval gate with thresholds, baseline, runner CLI, diff reporter |
| #19 | Gate self-test (proves regression detection) |
| #20 | GitLab CI agent-eval job |
| #21 | Sidecar Dockerfile pre-baked HF model weights |
| #22 | GitHub Actions eval mirror |
| #23 | Pre-commit eval-smoke hook (10-case <30s budget) |
| #26 | HTTP cache headers on InternalDocumentBytesController |
| #27 | Observability extensions (vision pricing, route_decisions, latency p50/p95) |
| #28 | Lab PDF E2E happy-path test |
| #29 | Intake form E2E happy-path test |
| #31 | W2 evaluation report (`docs/eval-report-2026-05-08.md`) |
| #35 | Defense Q&A primer (`docs/defense-qa-w2.md`) |
| #40 | Production W2 SupervisorAdapter + regenerate_baseline CLI |
