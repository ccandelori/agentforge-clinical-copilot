# Where we left off — 2026-05-05 (W2 prep landed; MVP work begun)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**W1 is closed; W2 is in flight on the MVP track.** W1-gaps closed
22/22 in the prior session. This session set up the W2 architecture
+ planning surface, GitLab CI from-scratch, fixed all pre-existing
PHPStan errors, and shipped the first MVP code task (Task 2 —
Citation contract Pydantic schemas).

**Current state: W2 task tag = 3/35 done (Tasks 36, 37, 2).**

The W2 PRD lives at `.taskmaster/docs/week2-prd.md`. Architecture
docs are `W2_ARCHITECTURE.md` (full design — authoritative) and
`W2_DEFENSE.md` (architecture-defense summary). Slide deck at
`docs/w2-defense-slides.html` (18 slides, scroll-snap HTML).

## What shipped this session

### Architecture + planning surface (committed to main via MR !1)

- `W2_ARCHITECTURE.md` — full design doc. Five load-bearing
  decisions (intake → QuestionnaireResponse, lab → procedure_*,
  LangGraph wiring, three PHI boundaries, eval gate is the
  deliverable). Three explicit tradeoffs.
- `W2_DEFENSE.md` — defense-ready summary + anticipated questions.
- `docs/w2-defense-slides.html` — 18-slide HTML deck with SVG
  topology + sequence diagrams (intake flow, lab flow, supervisor
  routing, eval gate run).
- `.taskmaster/docs/week2-prd.md` — PRD for `parse-prd`.
- `.taskmaster/tasks/tasks.json` — week2 tag with 35 tasks, MVP
  priority scoping applied (see "MVP scope" below).
- `docs/agents/{domain,issue-tracker,triage-labels}.md` — agent
  guides referenced from CLAUDE.md.
- `AGENTS.md` — Codex-flavored sibling of CLAUDE.md.

### GitLab CI from scratch (MR !1)

- `.gitlab-ci.yml` — single `test` stage with 4 jobs: phpstan,
  phpunit-isolated, sidecar-pytest, sidecar-regression-locks.
- Docker executor on a self-hosted local runner (Cameron's MacBook).
- Project-level "Pipelines must succeed" enforcement is ON via
  GitLab Settings → Merge requests.

### Tasks closed

| ID | Title |
|---|---|
| 36 | CI Bootstrap — GitLab pipeline runs every MR from day 1 |
| 37 | Fix 62 pre-existing PHPStan level-10 errors in W1 AgentForge |
| 2  | W2 Citation Contract Pydantic Schemas (+ 2.1, 2.2, 2.3, 2.4, 2.5) |

## Open MRs (your job to merge)

| MR | Branch | Status |
|---|---|---|
| **!2** | `feat/w2-task-37-phpstan-cleanup` | Pushed; review + merge |
| (none) | `feat/w2-task-02-citation-contract` | Pushed; **MR not yet created** |

Create the missing MR via:
```
glab mr create --target-branch main --fill
```
or
```
https://labs.gauntletai.com/cameroncandelori/openemr/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat/w2-task-02-citation-contract
```

After both merge, the Task 5 starter branch should be off the most
recent main.

## MVP scope (22 tasks of 35)

Per the W2 MVP review, only 22 of 35 W2 tasks are MVP-blocking.
Priorities have been re-set in tasks.json so `task-master next`
walks the MVP first.

```
HIGH (MVP — 22):
  Foundation:  1, 2✓, 3, 4, 14
  Lab path:    5, 6, 7, 8, 11
  Intake path: 12, 13
  Hybrid RAG:  9, 10, 15, 21
  Overlay UI:  24, 25
  Smoke:       28, 29
  Ship:        30, 34

MEDIUM (soft-MVP):
  33  demo video (sticky note for end-of-week)

LOW (tier-2 — defer):
  16, 17, 18, 19  eval gate, judge, self-test
  20, 22, 23      eval-gate CI integration
  26              HTTP cache headers
  27              observability extensions
  31, 32          eval / cost-latency reports
  35              defense Q&A primer
```

## Next moves (post-merge)

`task-master next` recommends **Task 5 — Seed AgentForge Intake
Questionnaire via Doctrine Migration** (high, complexity 5, no deps).

Other unblocked MVP starters: 7, 10, 14.

The dependency-aware MVP execution order is roughly:
- **First wave (parallel)**: 5, 7, 10, 14 — no dependencies
- **After 1 lands**: 15, 11, 13 (workers)
- **After 2 lands** (already done): 3, 4, 9 unblock
- **After ingest tasks land**: 24, 25 (overlay), 28, 29 (smoke)
- **Critical path tail**: 30 (deploy), 34 (README)

Task 1 (LangGraph supervisor refactor) is the architectural anchor
— complexity 9. Worth a focused session. The 9 W1 regression locks
must stay green through the refactor.

## Architectural decisions to honor

- **Citation contract** (Task 2 done): `Citation.page_bbox` is
  required for `LAB_PDF` and `INTAKE_FORM`; validator rejects
  `bbox_confidence < 0.7`. Constant exported as
  `SCANNED_SOURCE_BBOX_CONFIDENCE_FLOOR`. Lower-confidence fields
  belong in `unsupported_fields` (handled by callers, not as
  structured Citations).
- **Page indexing is 1-indexed** throughout (`PageBBox.page >= 1`,
  matches pdf.js native semantics). Don't subtract 1 anywhere in
  the overlay code.
- **Migration idempotency** for Task 5: use SELECT-then-INSERT-or-
  UPDATE on `questionnaire_repository.source_url`. That column has
  NO unique index in this fork (verified `sql/database.sql:14342`),
  so DB-level upsert is unavailable. Migration path is root
  `db/Migrations/`, not under the module.
- **Persistence endpoints** (Tasks 8, 12) must triple-check
  `JWT.patient_id == request.patient_id == documents[doc_id].patient_id`.
  Reject 403 on any mismatch. The third check (document-belongs-to-
  patient) catches forged document_id replay.
- **Browser upload route** (Task 6) derives patient_id from the
  active OpenEMR session, NEVER from multipart payload. If both
  present, verify they match.
- **PDF rendering** (Task 11) uses **PyMuPDF** (`pymupdf` in
  pyproject.toml), NOT pdf2image. PyMuPDF needs no Poppler system
  package and is faster.
- **Citation overlay** (Tasks 24, 25) is **vanilla JS, not React**.
  Vendor pdf.js bundle at `oe-module-agentforge/public/vendor/pdfjs/`
  (pinned 4.x; download official prebuilt). Document URL must
  include `&as_file=false` so OpenEMR serves bytes inline.
- **Three trust boundaries** for PHI: Browser↔OpenEMR (session+CSRF),
  OpenEMR↔Sidecar (JWT, bytes in memory only), Sidecar↔Anthropic
  (BAA, **PHI crosses here** — the load-bearing exception).

## Local dev gotchas

- **PHP not installed on host.** Run phpstan/phpunit via Docker:
  ```
  cd docker/development-easy && docker compose exec openemr bash -c 'cd /var/www/localhost/htdocs/openemr && composer phpstan'
  ```
  The docker container is `development-easy-openemr-1` — needs to
  be running (`docker compose up --detach --wait`).

- **`sites/default/sqlconf.php` is `--skip-worktree`** on this
  checkout. Local override (host=`mysql`, `$config=1`) is
  preserved; git status hides it. Undo with
  `git update-index --no-skip-worktree sites/default/sqlconf.php`.

- **GitLab Runner** is Homebrew user-mode service (`brew services
  list` shows `gitlab-runner`). Config at
  `~/.gitlab-runner/config.toml`. Two important settings:
  - `concurrent = 1` (sequential — phpstan needs full VM RAM)
  - `host = "unix:///Users/sheep/.docker/run/docker.sock"` under
    `[runners.docker]` (Docker Desktop puts the socket here, not
    `/var/run/docker.sock`)

- **Docker Desktop allocated 16 GB** (bumped from 8 GB to fit
  PHPStan's 4 GB + phpunit). Configured at
  `~/Library/Group Containers/group.com.docker/settings-store.json`
  → `MemoryMiB: 16384`.

- **Taskmaster gotcha**: `task-master set-status` re-stringifies
  task IDs (turns int 1 into string "1"). After running it, run:
  ```python
  python3 -c "
  import json
  p = '.taskmaster/tasks/tasks.json'
  d = json.load(open(p))
  for t in d['week2']['tasks']:
    if isinstance(t.get('id'), str): t['id'] = int(t['id'])
    deps = t.get('dependencies', [])
    t['dependencies'] = [int(x) if isinstance(x, str) else x for x in deps]
  json.dump(d, open(p, 'w'), indent=2)
  "
  ```
  to re-normalize. Otherwise `add-dependency` chokes.

- **Taskmaster gotcha #2**: `task-master update-task --id=N` uses
  AI to regenerate the task body and can DROP sibling tasks added
  via direct JSON edit. If you have local-only task additions, edit
  tasks.json directly with Python, not via update-task.

## Quick-start checklist for next time

1. `git status` — confirm clean working tree (sqlconf.php hidden by
   skip-worktree)
2. `git checkout main && git pull` — pick up merged MRs !2 and
   (eventually) the Task 2 MR
3. `task-master tags use week2 && task-master list` — confirm 3/35
   done with priorities applied
4. `task-master next` — should propose Task 5 (or 7, 10, 14 if
   you'd rather start in parallel)
5. Pick a task, branch off main: `git checkout -b feat/w2-task-NN-<slug>`
6. `task-master show NN` — full implementation steps
7. `task-master set-status --id=NN --status=in-progress`
8. Implement (TDD where applicable per the project's primary workflow)
9. Run tests:
   - Sidecar: `cd sidecar && uv run pytest`
   - PHP: `docker compose exec openemr bash -c '...composer phpunit-isolated'`
10. Verify both lint + types: `uv run ruff check && uv run mypy src/`

## Key files for the next MVP tasks

### Task 5 — Doctrine migration for AgentForge Intake Questionnaire
- `db/Migrations/Version20260504000001_seed_agentforge_intake_questionnaire.php` (NEW)
- Reference: existing migrations in `db/Migrations/`
- Schema: `sql/database.sql:14342` (`questionnaire_repository`)

### Task 7 — JWT-validated `get_document_bytes` endpoint
- `interface/modules/custom_modules/oe-module-agentforge/public/internal/get_document_bytes.php` (NEW)
- Pattern: existing `recent_encounters.php`, `procedures.php` etc.
- Use `AuthHeaderBridge::bridgeAuthorizationHeader()` (added Task 37)

### Task 10 — Commit clinical-guideline corpus
- `sidecar/data/guidelines/` (NEW directory)
- ~30 documents, ~600 chunks of ~500 tokens each
- ADA, JNC 8 / ACC-AHA, AHA/ACC lipids, CKD staging, common labs

### Task 14 — PHI redaction at LangfuseClient
- `sidecar/src/agentforge/observability/langfuse_client.py` (extend)
- Strip `messages[*].content` from log payloads on extraction-call
  type. Preserve latency, model, tokens, schema-validation result.

## What's deployed and where

`http://143.244.157.90:9300/` — production demo. Still running W1 code.
The droplet is now ~30+ commits behind main. Recommended: redeploy
after Task 30 lands (the W2 MVP deploy task).

## How this session ended

```
3 / 35 W2 tasks done (36, 37, 2 + Task 2 subtasks 2.1-2.5)
~5 commits across 2 feature branches
0 PHPStan errors across 4338 files (level 10)
2996 PHP isolated tests green (no regressions)
786 sidecar tests green + 8 pre-existing integration failures (unrelated)
20 new schema tests in 0.01s
```

Two branches awaiting merge (MR !2, plus Task 2 needs MR creation).
After both merge, the next session picks up at Task 5 (or any of
the other unblocked MVP starters: 7, 10, 14).
