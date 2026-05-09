# oe-module-agentforge

OpenEMR custom module for the **AgentForge Clinical Co-Pilot**.

This is the OpenEMR-side half of the integration. It registers the
`OpenEMR\Modules\AgentForge` namespace, mints the JWT used to call the
sidecar, and renders the agent panel inside the patient summary view.
The Python sidecar (FastAPI + LangGraph orchestrator) lives in
[`/sidecar/`](../../../../sidecar/) at the repository root. See
[`/ARCHITECTURE.md`](../../../../ARCHITECTURE.md) for the full system
topology.

## Pre-deploy gate: required database indexes

The agent depends on composite and FULLTEXT indexes on five OpenEMR tables
(`procedure_order`, `procedure_report`, `form_vitals`, `pnotes`,
`form_clinical_notes`). Without them, lab retrieval and note search miss
the agent's per-tool latency budget by an order of magnitude
(AUDIT.md P2 — confirmed against the demo dataset).

**These indexes are a hard precondition. The agent must not go live until
they are present.**

### Applying the indexes

The indexes are introduced by Doctrine migrations and are also baked into
`sql/database.sql`. Either path satisfies the gate:

| Install type | How indexes get applied |
|--------------|-------------------------|
| Fresh OpenEMR install | `setup.php` runs `sql/database.sql`, which contains the indexes inline. No extra step. |
| Existing OpenEMR install | Run the Doctrine migrations manually: `ENVIRONMENT=development ./cli migrations:migrate --no-interaction` |

Doctrine migrations are not yet auto-applied during OpenEMR upgrades
(per `db/README.md` — pending issue #10708 upstream), so the manual
`./cli migrations:migrate` step is required for any environment that
already has an OpenEMR schema in place.

The relevant migrations:
- `db/Migrations/Version20260430000001.php` — composite BTREE indexes
- `db/Migrations/Version20260430000002.php` — FULLTEXT indexes for note search

### Verifying the gate

A PHPUnit regression test asserts that all seven expected indexes exist
on the live database with the correct columns and `INDEX_TYPE`:

```bash
docker compose exec openemr bash -c \
    'cd /var/www/localhost/htdocs/openemr \
     && vendor/bin/phpunit --filter AgentForgeIndexesTest'
```

Expected output: `Tests: 7, Assertions: 21, OK`. Any failure means the
gate is unmet and the agent must not be enabled.

The test source: [`/tests/Tests/Common/Database/AgentForgeIndexesTest.php`](../../../../tests/Tests/Common/Database/AgentForgeIndexesTest.php).

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Could not detect environment name` from `./cli` | `ENVIRONMENT` env var not set | Run with `ENVIRONMENT=development ./cli migrations:migrate ...` |
| `Duplicate key name` on migration apply | Indexes already exist (e.g., from an earlier manual application) | Compare current state via `SHOW INDEX FROM <table>`. If correct, manually mark the migration as run: `./cli migrations:version Version20260430000001 --add` |
| Test fails: index missing | Migrations not yet applied to this DB | Apply per the table above |
| Test fails: wrong columns / type | Schema drift between this branch and the deployed DB | Investigate; do not patch the test to match drift — fix the schema |

### Known follow-up

`idx_procedure_report_date` leads with the table's PRIMARY KEY column,
which makes it redundant with the InnoDB clustered index. It was added
per the original task spec for parity; whether to drop it is tracked
as Taskmaster Task 49 (low priority, post-MVP).

## Pre-deploy gate: canonical intake-form Questionnaire

The intake-form persistence flow (W2 Task 12) writes a
`QuestionnaireResponse` against a single canonical `Questionnaire` row
identified by URL. That row must exist before any intake-form upload
can be persisted.

**Canonical URL:** `https://agentforge.openemr.org/Questionnaire/intake-form`

### Applying the seed

| Install type | How the Questionnaire row gets seeded |
|--------------|---------------------------------------|
| Fresh OpenEMR install | Apply the W2 Doctrine migration: `ENVIRONMENT=development ./cli migrations:migrate --no-interaction` |
| Existing OpenEMR install | Same as above — Doctrine migrations are not auto-applied during upgrades (issue #10708) |

Migration: `db/Migrations/Version20260505000001.php` — seeds one row in
`questionnaire_repository` with `source_url` set to the canonical URL,
a name of `AgentForge Intake Form`, status `active`, the FHIR logical
id `agentforge-intake-form` in `questionnaire_id`, and a FHIR R4
Questionnaire JSON whose `item` set mirrors the `IntakeFormExtraction`
Pydantic model (chief_concern, demographics, medications, allergies,
family_history). The logical id is also copied onto every persisted
`questionnaire_response.questionnaire_id` so FHIR clients can resolve
the canonical `Questionnaire/agentforge-intake-form` reference.

### Idempotency

`questionnaire_repository.source_url` has no DB-enforced unique index,
so the migration uses a `SELECT`-then-`INSERT`-or-`UPDATE` pattern in
`up()`. Re-running over an existing canonical row updates that row in
place rather than producing a duplicate. `down()` deletes by
`source_url` so the rollback is scoped to the canonical row only.

## Frontend dependencies

The citation overlay component (`public/js/citation_overlay.js`, Task 24)
renders PDFs using **pdf.js**. We consume it as an OpenEMR-level npm
dependency, not as a module-local vendor — pinned in the repository-root
`package.json` and copied to `/public/assets/pdfjs-dist/` by the gulp
install task.

| Library | Version | License | Served from |
|---------|---------|---------|-------------|
| `pdfjs-dist` | 5.7.284 (pinned) | Apache-2.0 | `/public/assets/pdfjs-dist/legacy/build/` |

**Setup:** after `npm install`, run `npx gulp -i` (or `npm run build -- -i`)
to populate `/public/assets/`. The `/public/assets/*` path is gitignored;
the dependency lives in the lockfile, not in the tree.

**Why pdf.js 5.x + ESM:** pdf.js 5.x is ESM-only. The legacy build
(`legacy/build/pdf.min.mjs`) provides broader browser-syntax compatibility
than the modern build but still ships as ECMAScript modules — `<script
type="module">` is required to load it.

**Why npm-managed instead of module-local vendor:** `W2_ARCHITECTURE.md` §3
called for a "vendored pdf.js bundle" because the alternative considered
was bundling pdf.js as part of a *sidecar-side* React/JSX component, which
would have required adding a Node toolchain to the Python sidecar. OpenEMR
already has a Node toolchain (gulp/npm) for its other vendored JS, so
adding pdf.js as a project-level npm dep matches the established
convention without contradicting the original ADR's intent. See
`docs/DEVIATIONS.md` (2026-05-06) for the longer rationale.

## Layout

```
oe-module-agentforge/
├── openemr.bootstrap.php       # OpenEMR module entry point
├── moduleConfig.php            # Module Manager config hook
├── info.txt                    # Module display name
├── src/
│   ├── Bootstrap.php           # Event subscription + Twig wiring
│   ├── Controllers/            # Request handlers (Task 2+)
│   ├── Services/               # Module service classes (Task 6+)
│   └── Events/                 # Custom event definitions
├── templates/
│   └── agent_panel.html.twig   # Patient-summary chat panel
├── public/                     # Public-facing entry points and JS
└── tests/                      # Module-scoped PHPUnit tests
```

## License

GPL-3.0-or-later. See the repository root `LICENSE` file.
