# Where we left off — 2026-05-05 (W2 MVP shipped end-to-end)

Read me first when picking the project back up. Update or delete me
when the state captured here goes stale.

## Headline

**The W2 MVP deliverable is shipped on `main`.** All four bullets
("Lab PDF and intake form ingestion working locally; first extraction
and first evidence retrieval demo") are live and demoable today.
**11/37 W2 tasks done.** Both ingestion endpoints accept JWT-validated
JSON and write atomically into OpenEMR. The vision-extraction tool
runs against a bundled mock lab PDF with zero setup beyond an
Anthropic API key. The evidence retriever indexes a 5-doc /
29-chunk corpus over BM25.

The session that produced this state shipped **15 MRs** today (not
counting prep MRs from the morning). Repo state is fully clean —
zero open MRs, zero stale branches awaiting merge.

## Demo recipes (zero-setup)

```bash
# Both endpoints are live on the openemr Docker container:
#   POST /agentforge/internal/persist_lab_result
#   POST /agentforge/internal/persist_questionnaire_response
# Both accept the structured JSON shape from sidecar/src/agentforge/schemas/

# Vision-extraction demo (extracts the bundled mock lab PDF):
cd sidecar
export ANTHROPIC_API_KEY=...
uv run python scripts/extraction_demo.py
#   → 1 page, ~3.4K input tokens, structured LabPdfExtraction output
#   → 20 lab values across 4 panels (Diabetes, CMP, Lipid, CBC)
#   → bbox citations on every value

# Evidence-retrieval demo:
uv run python scripts/retrieval_demo.py "ASCVD risk statin therapy"
uv run python scripts/retrieval_demo.py "A1C target for adult diabetes"
uv run python scripts/retrieval_demo.py "CKD stage 3 management"
#   → BM25 top-3 with chunk metadata + excerpts
```

## What shipped today

| Task | What | Where it lives |
|---|---|---|
| 3 | Lab extraction Pydantic schemas | `sidecar/src/agentforge/schemas/lab.py` (LabValue, LabPdfExtraction, AbnormalFlag) |
| 4 | Intake extraction Pydantic schemas | `sidecar/src/agentforge/schemas/intake.py` (IntakeFormExtraction + 4 leaf models) |
| 5 | Canonical Questionnaire DB seed | `db/Migrations/Version20260505000001.php` |
| 7 | JWT-validated `get_document_bytes` endpoint | `oe-module-agentforge/public/internal/get_document_bytes.php` |
| 8 | JWT-validated `persist_lab_result` endpoint | `oe-module-agentforge/public/internal/persist_lab_result.php` (multi-table cascade in DBAL transaction) |
| 10 | Clinical-guideline corpus + BM25 demo | `sidecar/data/guidelines/` + `sidecar/scripts/{chunk_guidelines,retrieval_demo}.py` |
| 11 | Vision-extraction tool (Claude vision + PyMuPDF) | `sidecar/src/agentforge/tools/attach_and_extract.py` + `sidecar/scripts/extraction_demo.py` |
| 12 | JWT-validated `persist_questionnaire_response` endpoint | `oe-module-agentforge/public/internal/persist_questionnaire_response.php` |
| (extra) | Mock-lab PDF generator + bundled fixture | `sidecar/scripts/generate_mock_lab.py` + `sidecar/data/samples/sample-lab.pdf` |
| (P2 fixes) | Inverted bbox, message leak, case-insensitive auth, legacy doc exceptions | MR !11 |

## Reusable services extracted today

These are part of the AgentForge module's DI-able service surface;
Task 13's intake-vision variant and any future endpoint should
import these rather than re-implement:

- `DocumentOwnershipVerifier` (`src/Services/`) — single SELECT against
  `documents.foreign_id` with `deleted=0` filter; returns null on
  missing/deleted/null-owner
- `IntakeQuestionnaireLookup` (`src/Services/`) — finds the canonical
  Questionnaire by `source_url`; returns a `SeededIntakeQuestionnaire`
  DTO (id + name + frozen JSON snapshot)
- `IntakeFormFhirMapper` (`src/Services/`) — pure transform:
  IntakeFormExtraction array → FHIR R4 QuestionnaireResponse
- `IntakeQuestionnaireResponseWriter` (`src/Services/`) — single INSERT
  into `questionnaire_response` with UUID generation
- `IntakePersistAuditWriter` / `LabPersistAuditWriter` (`src/Services/`) —
  fire `agentforge.questionnaire_persist` / `agentforge.lab_persist`
  events on success only
- `LabResultWriter` (`src/Services/`) — multi-table cascade INSERT
  (procedure_order → procedure_report → N×procedure_result) inside
  a DBAL transaction
- `LabResultIds` / `DocumentBytesResult` / `SeededIntakeQuestionnaire` /
  `LabValue` / `LabPdfExtraction` — frozen DTOs

## Next moves

`task-master next` proposes **Task 14** (PHI Redaction at LangfuseClient).

Other unblocked MVP tasks:

- **Task 13** — Intake-form vision variant (cx=5, deps 4+11+12 — all
  met). Same renderer as Task 11; different prompt + schema
  (IntakeFormExtraction). Mostly a copy-and-adapt of
  `attach_and_extract.py`. **First-up if continuing the W2 path.**
- **Task 14** — PHI redaction at LangfuseClient (cx=4). Adds a
  redaction step on extraction-call observability so PHI from the
  vision tool's prompts/responses doesn't leak into Langfuse traces.
- **Task 15** — Evidence retriever LangGraph node (cx=6, deps 1+9+10
  — needs Task 1 supervisor refactor first, NOT met). Wires the
  Task 10 corpus into the agent loop.
- **Task 1** — LangGraph supervisor refactor (cx=9). The
  architectural anchor. Blocks Tasks 13's full integration into
  the agent loop, plus Task 15.

Critical path for full W2 (not just MVP): Task 1 → Task 15 → eval-gate
tasks (16-19). MVP is shippable today without those.

## Architectural decisions to honor

- **Citation contract** (Task 2): every clinical claim carries a
  `Citation` with the bbox-confidence floor (0.7) and inverted-bbox
  rejection enforced at the schema layer. Any new extractor MUST
  produce Citations, not free-form text references.
- **Page indexing is 1-indexed** throughout (`PageBBox.page >= 1`,
  matches pdf.js native semantics). Don't subtract 1 anywhere in
  overlay code.
- **Triple-check at every persistence endpoint:** `JWT.patientId ==
  request.patient_id == documents.foreign_id`. All four mismatch
  shapes collapse to 403 (no information disclosure about which
  leg failed). Reuse `DocumentOwnershipVerifier`.
- **Audit fires only on success.** 1:1 correspondence with rows
  actually written. No orphan events on 401/403/500, no double on retry.
- **No PHI in logs.** Validation-failure logs use `error_count`, not
  the failing payload. Module-level loggers emit only structural
  metadata (page count, model, tokens). Langfuse-side redaction is
  Task 14's surface.
- **No structured EHR table writes from AI persistence.** The
  unapproved record (`questionnaire_response`, `procedure_*`) is
  what the overlay reads; structured tables (`patient_data`,
  `lists`, `medications`, `allergies`, `family_history`) only get
  written when a clinician approves on the overlay UI.
- **JSON-shape-as-tool pattern for vision.** The vision tool uses
  `tool_choice={"type": "tool", "name": "..."}` to coerce structured
  output. Inline schema (not Pydantic-reflected) so a Pydantic field
  rename can't silently reshape the LLM emission contract.
- **Bytes stay in memory.** PDF rendering never writes a temp file;
  `fitz.open(stream=...)` reads from a buffer. Base64 PNG strings
  live on the request stack and are released when the call returns.
- **Narrow catch (DbalException | JsonException | RuntimeException)
  on write paths.** The project's `ForbiddenCatchTypeRule` blocks
  Throwable / Exception (those would suppress Error / ErrorException).
  Programmer bugs propagate to the global handler.

## Local dev gotchas (accumulated this session)

- **`task-master set-status` re-stringifies task IDs** as a side
  effect, blowing up `add-dependency` and inflating the diff to
  ~600 lines. After every status flip, run the renormalization
  script (committed as inline Python in every status-sync MR; see
  e.g. !15 commit body). The recipe converts pure-digit string IDs
  back to ints.
- **PHPStan cache surfaces stale baseline drift inconsistently.**
  We saw this twice: an [OK] run, then later a "Found 8 errors"
  run with phantom "ignore pattern X was not matched" warnings on
  legacy library files we never touched. Fix:
  `rm -rf tmp-phpstan/cache && composer phpstan` clears it. The
  errors don't reproduce in CI — only in the long-lived dev
  container's cache. Don't chase them as MR-blocking.
- **`Document::get_data()` throws BadMethodCallException +
  RuntimeException** on legacy storage edge cases (expired, deleted,
  missing-file, decrypt-failure). The docblock only declares one
  of the two — the other is real but undocumented. The
  `DocumentBytesRepository` catches both with a justified
  `@phpstan-ignore catch.neverThrown` on the second; keep this
  pattern when reusing the legacy class.
- **PHP not installed on host.** All composer scripts run via
  `docker exec development-easy-openemr-1 bash -c '...'`.
- **`sites/default/sqlconf.php` is `--skip-worktree`** — keeps the
  local override (host=`mysql`, `$config=1`) but hidden from
  git status. Undo with `git update-index --no-skip-worktree`.
- **Anthropic SDK requires 256-bit HMAC keys** in tests. A 31-char
  test secret = 248 bits and fails JWT signing. Use a 32-char
  alphanumeric placeholder in fixtures.
- **PDF generator is deterministic** via ReportLab's `invariant=1`.
  Re-running `scripts/generate_mock_lab.py` produces byte-identical
  PDFs — safe to commit the output.
- **CI runner is `concurrent = 1`** — back-to-back MRs serialize.
  Pipeline timing budget: ~5–6 min when the runner is idle, ~10
  min when contended. Worst case observed: 2-hour queue gap (rare,
  presumably runner restart or contention).

## Quick-start checklist

1. `git status` — confirm clean working tree (sqlconf.php hidden)
2. `git checkout main && git pull` — should be clean (no open branches)
3. `task-master tags use week2 && task-master list` — confirm 11/37 done
4. `task-master next` — should propose Task 14
5. Pick a task, branch off main:
   `git checkout -b feat/w2-task-NN-<slug>`
6. `task-master show NN` — full implementation steps
7. Implement (TDD where applicable)
8. Run tests:
   - Sidecar: `cd sidecar && uv run pytest`
   - PHP: `docker exec development-easy-openemr-1 bash -c 'cd /var/www/localhost/htdocs/openemr && composer phpunit-isolated'`
9. Lint + types: `uv run ruff check && uv run mypy`
10. PHPStan via Docker:
    `docker exec development-easy-openemr-1 bash -c 'cd /var/www/localhost/htdocs/openemr && composer phpstan'`
11. Commit, push, MR. Do NOT include tasks.json edits in feature MRs —
    bundle status flips into a separate `chore/w2-status-sync-N` MR.

## Key files for the next likely tasks

### Task 13 — Intake-form vision variant (cx=5)

- **Reuse:** `sidecar/src/agentforge/tools/attach_and_extract.py`
  (`PdfRenderer` is shape-identical for intake forms)
- **Adapt:** the `VisionExtractor` class — different system prompt
  (intake-form-shaped, references chief_concern + demographics
  + medications + allergies + family_history); different tool spec
  (mirrors `IntakeFormExtraction` schema instead of LabPdfExtraction);
  different return type (`IntakeFormExtraction`)
- **Recommended layout:** either parameterize the existing
  `VisionExtractor` over the schema/prompt pair, or extract a base
  `_VisionExtractorBase` and subclass for lab vs intake. The latter
  is cleaner; the former is less code. Pick based on whether Task 15
  will introduce a third vision flow.
- **Demo:** add `scripts/intake_extraction_demo.py` mirroring
  `extraction_demo.py`; the mock-lab generator should grow a
  companion `scripts/generate_mock_intake.py` if you want zero-setup
  demoability.

### Task 14 — PHI redaction at LangfuseClient (cx=4)

- **Locate:** `sidecar/src/agentforge/observability/langfuse_client.py`
  (per Taskmaster spec; verify the path)
- **Strip from log payloads on extraction-call type:**
  `messages[*].content` (the rendered images and the structured
  output) — preserve `model`, `latency`, `tokens`, `schema_validation_result`
- **Test:** mock the Langfuse client, send a fake extraction call
  through, assert the captured trace contains structural metadata
  but NOT the prompt/response bodies

## What's deployed where

`http://143.244.157.90:9300/` — production demo droplet. **Still
running W1 code.** The droplet is now ~30+ commits behind main.
**Recommended: redeploy after Task 30 lands** (the W2 MVP deploy
task), or if you want the demo droplet to reflect today's MVP work,
redeploy now via `docs/DEPLOYMENT.md`.

## How this session ended

```
11 / 37 W2 tasks done (2, 3, 4, 5, 7, 8, 10, 11, 12, 36, 37)
15 MRs merged today (!3 through !18)
0 phpstan errors across 4358 files
857 PHP isolated tests + sidecar (838 baseline + 19 new)
0 sidecar regressions (8 pre-existing integration failures unchanged)
```

All four MVP deliverable bullets shipped. Both demos zero-setup. Repo
state pristine. Pick up at Task 13 or Task 14 next session.
