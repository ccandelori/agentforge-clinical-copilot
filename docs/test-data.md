# AgentForge test data — seed pipeline

This doc captures how we generate synthetic test data for the demo and
the eval framework. The MVP seed pipeline is **Synthea (bulk
structured + note data) + a hand-crafted SQL overlay (sensitivity edge
cases for designated demo patients)**. See Taskmaster task 50 for the
work breakdown.

## Why we need synthetic data

The default OpenEMR demo DB ships with 3 patients, each with one
encounter dated **2014-02-01**, **0 clinical notes**, and **0 lab
results**. Three of the agent's nine tools (`get_recent_notes`,
`search_notes`, `get_recent_labs`) cannot meaningfully demo on this
dataset, and the eval-fixture phenotype labels (e.g. "Susan Underwood
= complex chronic") don't match the real DB. Seeding fixes both gaps.

## Tool choice — Synthea

[Synthea](https://github.com/synthetichealth/synthea) (MITRE) is an
open-source synthetic patient generator. Peer-reviewed (Walonoski et
al., JAMIA 2018), calibrated against CDC / NIH / AHRQ statistics, used
by CMS and ONC for FHIR conformance testing. It produces statistically
plausible populations — **not** clinically realistic individual
records at the narrative level. See "Honest framing" at the bottom.

**Pinned version:** `v4.0.0` (released 2025).

**Install location on this dev machine:** `~/Desktop/Gauntlet/synthea/`
(sibling to the openemr repo — see project memory note).

## Spike findings — 2026-05-02 (subtask 50.1)

```bash
cd ~/Desktop/Gauntlet/synthea
git checkout v4.0.0
./gradlew build -x test    # ~60s
./run_synthea -p 5 --exporter.ccda.export=true --exporter.fhir.export=true
# Output written to ./output/{ccda,fhir,metadata}/
```

Sample run: 5 patients, ages 12-68, MA-based, ~4s wall time. Output:

| Format | Per-patient size | Where it lives in output/ |
|---|---|---|
| CCDA (XML) | ~720 KB | `output/ccda/<name>_<uuid>.xml` |
| FHIR R4 (JSON bundle) | ~3 MB | `output/fhir/<name>_<uuid>.json` |

### What's in a CCDA file

Sections present in a sample CCDA (from `<title>` tags):

- Allergies, Medications, Diagnostic Results, Problems, Surgeries,
  Encounters, Vital Signs, Immunizations, Plan of Care, Social
  History, Functional Status

### What's in a FHIR bundle

A 46-encounter sample bundle:

| Resource | Count | Maps to OpenEMR |
|---|---|---|
| Observation | 361 | `form_vitals`, lab results |
| Procedure | 151 | `procedure_*` tables |
| DiagnosticReport | 97 | lab reports |
| MedicationRequest | 49 | `lists` (medication) |
| Encounter | 46 | `form_encounter` |
| **DocumentReference** | **46** | **`pnotes` / `form_clinical_notes`** ← key for notes demo |
| Condition | 38 | `lists` (medical_problem) |
| Immunization | 15 | `immunizations` |
| AllergyIntolerance | (not in this sample, but generated for some patients) | `lists` (allergy) |

### Critical CCDA vs FHIR finding

**CCDA does not include clinical notes.** Its `<text>` blocks are
HTML display-narratives wrapping the structured data tables (per
the C-CDA R2.1 spec), not free-text clinical notes. Importing only
CCDAs leaves `pnotes` / `form_clinical_notes` empty.

**FHIR DocumentReference resources DO include note text.** Sample
note (decoded from base64-encoded `attachment.data`):

```
1975-11-05

# Chief Complaint
No complaints.

# History of Present Illness
Andrea7 Latonya462 is a 18 year-old hispanic white female.

# Social History
 Patient has never smoked.
 Patient identifies as homosexual.
[...]

# Allergies
No Known Allergies.

# Medications
No Active Medications.

# Assessment and Plan
Patient is presenting with housing unsatisfactory (finding) [...]
```

Templated but structured Markdown, LOINC-coded note types
("History and physical note" / "Evaluation + Plan note"), ~500–1500
chars per note. Long enough for FULLTEXT MATCH to find search terms,
short enough to render in the chat panel.

### Date range

Sample CCDA spans **2016 → 2026-05-06** (today is 2026-05-02). Synthea
uses "now" as the reference time by default and back-fills patient
history. The default 365-day lookback in our tools naturally includes
the most recent encounters — **no `--exporter.years_of_history`
override needed**.

### Implications for subtask 50.2 (importer probe)

The CCDA / FHIR finding reshapes subtask 2:

1. **CCDA path** via OpenEMR's built-in CCDA importer → expect to
   populate structured tables (encounters, problems, meds, allergies,
   vitals, labs). Notes will NOT come from this path.
2. **FHIR DocumentReference path** is needed for notes. OpenEMR's
   FHIR API is primarily a server (export); we need to verify whether
   POST endpoints accept `DocumentReference` resources, or whether we
   need a custom MariaDB loader that parses the FHIR JSON bundle and
   writes to `pnotes` / `form_clinical_notes` directly.

The subtask 2 probe should test both paths with the same single
patient and document which tables each populates. The hand-crafted
overlay in subtask 4 then fills the gap for sensitivity edge cases
that Synthea won't model (psych note types, attending-only flags).

## Honest framing on data realism

What this seed pipeline gives us:

- ✅ Plausible-enough volume to demo all 9 tools
- ✅ Internally consistent diagnoses + meds + observations within a
  patient (Synthea's state machines pair them)
- ✅ Recent encounter dates (within 365 days)
- ✅ FULLTEXT-searchable note bodies
- ✅ Citation-groundable: agent retrieves real-shaped data, real IDs

What it does NOT give us:

- ❌ Clinician-narrative-quality note text (templated phrasing — a
  real clinician spotting "Patient identifies as homosexual." in
  Social History will know it's synthetic)
- ❌ Realistic drug-interaction or rare-condition modeling
- ❌ A claim that the population matches any specific real EHR's
  distribution
- ❌ Research-grade synthetic data fit for clinical-judgment
  evaluation (use de-identified real corpora for that)

The seed is fit for **demo + regression-lock eval**. It is **not**
fit for **research-grade clinical-decision-support evaluation**.

## CCDA importer probe — 2026-05-02 (subtask 50.2)

OpenEMR ships a Symfony console command for single-shot CCDA import:

```bash
docker exec development-easy-openemr-1 \
  php /var/www/localhost/htdocs/openemr/bin/console \
  openemr:ccda-newpatient-import \
  --document=/tmp/synthea-test.xml --site=default
```

(Source: `src/Common/Command/CcdaNewpatientImport.php`. Calls into the
Carecoordination module's `CarecoordinationTable::importNewPatient()`
which lives at
`interface/modules/zend_modules/module/Carecoordination/`.)

**Don't pass `--debug`** — debug mode is interactive and prompts
per-immunization, which deadlocks any non-TTY pipeline. Non-debug
runs through silently.

### Coverage on a single Synthea CCDA (Andrea7 Schumm995, 68 y/o, 46 encounters)

| Table | Rows | Date range | Notes |
|---|---|---|---|
| `form_encounter` | **46** | 1975 → 2026-01-07 | Recent encounters within 365-day window ✅ |
| `lists` (medical_problem) | **38** | 1975 → 2025-12-24 | ✅ |
| `lists` (medication) | **8** | 2015 → 2023-09-08 | ⚠ Latest med is >2 years old; may not appear as "active" |
| `lists` (allergy) | 0 | — | Source patient has no allergies; not an importer gap |
| `form_vitals` | **1** | 2016-11-02 only | ⚠ Source CCDA had 62 vital observations; importer only kept 1 |
| `immunizations` | 15 | — | ✅ |
| `procedure_order` | 170 | — | ✅ |
| `procedure_report` | 19 | 2016 → 2025-12-31 | ✅ |
| `procedure_result` | 267 | — | ✅ ~14 results per report (lab panels) |
| **`pnotes`** | **0** | — | ❌ Expected — CCDA has no notes |
| **`form_clinical_notes`** | **0** | — | ❌ Expected — CCDA has no notes |

### Gaps to address downstream

1. **Notes** (`pnotes` / `form_clinical_notes`): CCDA path leaves
   these empty. We need a FHIR DocumentReference loader. Two
   plausible paths — defer choice to subtask 50.3:
   - (a) OpenEMR FHIR R4 server's POST endpoint (respects OAuth2,
     validation; requires OAuth2 client setup).
   - (b) Custom Python loader that parses the FHIR bundle and
     INSERTs into `pnotes` / `form_clinical_notes` directly via
     mariadb (faster spike, bypasses validation).
2. **Vitals are over-collapsed** by the importer (62 source
   observations → 1 row). For the vitals trend demo this is too
   sparse. Options: (a) accept and supplement with hand-crafted
   vitals overlay, (b) extend the FHIR loader to also write vitals
   from FHIR Observation resources. Defer to 50.3.
3. **Medication recency** depends on Synthea's individual patient
   profiles. With ~20 patients we expect a mix; some will have
   currently-active meds, some won't. Acceptable for the demo.
4. **Allergies** are patient-specific in Synthea — sample patient
   had none. With ~20 patients we expect roughly half to have at
   least one allergy. Validate post-batch.

### Subtask 50.2 verdict

Built-in CCDA import path works and covers 8 of 11 target tables
robustly. Notes are the only structural gap (expected from subtask
50.1's CCDA / FHIR analysis). Vitals over-collapse is a quality
concern, not a correctness blocker. Proceed to 50.3 (batch import)
with an extended scope to also wire a FHIR DocumentReference loader
for notes.

## Production seed — 2026-05-02 (subtask 50.3)

### Generation

```bash
cd ~/Desktop/Gauntlet/synthea
./run_synthea -p 20 \
  --exporter.ccda.export=true \
  --exporter.fhir.export=true \
  --exporter.baseDirectory=./output_20patients
# Records: total=25, alive=20, dead=5  (Synthea includes deceased
# patients to populate historical context — all 25 export OK.)
```

### CCDA batch import (structured data)

```bash
docker exec development-easy-openemr-1 mkdir -p /tmp/synthea-batch
docker cp ~/Desktop/Gauntlet/synthea/output_20patients/ccda/. \
  development-easy-openemr-1:/tmp/synthea-batch/
docker exec development-easy-openemr-1 sh -c '
  cd /var/www/localhost/htdocs/openemr
  for f in /tmp/synthea-batch/*.xml; do
    php bin/console openemr:ccda-newpatient-import \
      --document="$f" --site=default >/dev/null 2>&1 \
      && echo "ok: $(basename $f)" \
      || echo "FAIL: $(basename $f)"
  done
'
# 25/25 imported in ~3 min, 0 failures.
```

### FHIR DocumentReference loader (notes)

CCDA has no notes; FHIR's DocumentReference resources do. Path chosen:
**custom Python loader directly to MariaDB**, not OpenEMR's FHIR
server POST endpoint. Reasons:

1. The loader is one-shot seed tooling, not a recurring runtime
   integration. Bypassing OAuth2 + FHIR validation is fine for seed.
2. Faster to spike. We control exactly which pnotes columns get
   filled.
3. We can iterate on idempotency / dedup logic without re-deploying
   anything.

```bash
uv run scripts/seed/load_synthea_notes.py \
  --fhir-dir ~/Desktop/Gauntlet/synthea/output_20patients/fhir
# matched=25  missing_pid=0  inserted=880  skipped_existing=2
```

The script is idempotent on `(pid, date, title)` — re-runs are safe.
Notes go into `pnotes` only (not `form_clinical_notes`); the agent's
`NotesRepository` UNIONs both tables, so one is sufficient for the
demo. `form_clinical_notes` requires a parent encounter linkage via
the `forms` table that doesn't fall out cleanly from FHIR
DocumentReference shape.

### Patient name matching gotcha

OpenEMR's CCDA importer stores Synthea's patient names inconsistently:

- Patients with one given name → `fname = "Andrea7"`
- Patients with multiple given names → `fname = "Kandi717 Maryellen651"`
  (full space-joined string)

The loader tries both candidates per FHIR Patient resource and falls
back to the second if the first misses. All 25 patients matched on
the second dry-run.

### Final cohort counts

| Table | Rows | Within last 365d | Notes |
|---|---|---|---|
| `patient_data` | 25 (+3 demo) | n/a | 5 deceased, 20 alive |
| `form_encounter` | 882 | (most recent goes to 2026-04-28) | avg 35/patient |
| `lists` (problem) | 720 | n/a | |
| `lists` (medication) | 133 | mixed | some patients have currently-active meds, some don't |
| `lists` (allergy) | 33 | n/a | ~half the patients have ≥1 allergy |
| `form_vitals` | 25 | mostly old | 1 row/patient — importer collapses (known) |
| `immunizations` | 348 | mixed | |
| `procedure_result` | 3,341 | recent OK | ~14 per report (lab panels) |
| **`pnotes`** | **880** | **73** | populated by the FHIR loader |
| `form_clinical_notes` | 0 | — | not loaded; UNION in NotesRepository covers via pnotes |

### Designated demo patients

Picked from the cohort by data richness, lining up with the
eval-fixture phenotype labels:

- **pid=8 — Eula461 Crist667, 53F** — *complex chronic*. 16 recent
  encounters, 55 problems, 13 meds, 11 allergies, 16 recent notes.
  Replaces Susan Underwood (pid=2) as the eval's complex-chronic
  exemplar. **Susan stays in the DB** for backward-compat with the
  old smoke-test scripts.
- **pid=4 — Alena861 Marquardt819, 35F** — *sparse but active*. 1
  recent encounter, 17 problems, 0 meds, 0 allergies, 1 recent note.
  Replaces "Alex Newman" (pid=200, eval-fixture-only) as the sparse
  exemplar.

These are the patients the hand-crafted SQL overlay (subtask 50.4)
will target.

### What's still weak

- **Vitals** — 1 reading per patient is not a "trend." `get_vitals_trend`
  will return 1 datapoint per patient. Worth supplementing later
  via either (a) extending the FHIR loader to also read Observation
  resources for vital-sign LOINC codes, or (b) hand-crafted vitals
  overlay in the same SQL fixture as 50.4.
- **`form_clinical_notes`** — empty by design (see above). Means we
  can't demo a clinical-notes / pnotes split, but the
  `get_recent_notes` tool returns a unified list anyway.

## Demo overlay — 2026-05-02 (subtask 50.4)

Hand-crafted notes + vitals on top of the Synthea seed for the two
designated demo patients. Single SQL file at
`scripts/seed/agentforge_demo_overlay.sql`. Idempotent on
`user = 'agentforge-overlay'` — re-runs DELETE-then-INSERT cleanly.

```bash
docker exec -i development-easy-mysql-1 \
  mariadb -uopenemr -popenemr openemr \
  < scripts/seed/agentforge_demo_overlay.sql
# pnotes overlay inserted: 6
# form_vitals overlay inserted: 10
```

### What the overlay adds

| Patient | Notes | Vitals readings |
|---|---|---|
| pid=8 Eula Crist | 4 (1 standard, 2 SUD-gated, 1 search-target) | 5 (12-month BP/weight trend, hypertension under treatment) |
| pid=4 Alena Marquardt | 2 (1 standard, 1 phone-call) | 5 (12-month healthy baseline) |

### Sensitivity-rule coverage demoed

The overlay reaches **`substance_abuse_cfr42`** via `note_title_prefixes`:

- `SUD: Outpatient counseling session` (pid=8)
- `Substance Abuse: Initial intake` (pid=8)

A user lacking `cfr42_authorized` clearance will see these notes
appear with `permission_denied=True`, body/title/author stripped.

### Sensitivity-rule coverage NOT demoed

Two policy rules from `sensitivity_policy.yaml` cannot be exercised
through pnotes:

- **`behavioral_health`** gates by `form_encounter.pc_catid` (encounter
  category). It fires on the encounters tool, not notes. Demoing it
  requires creating an encounter with `pc_catid=11` or `12` for one of
  our patients — out of scope of the note-focused overlay.
- **`attending_only`** requires a `notes_meta` table extension
  (deployment-added per ARCHITECTURE.md §2). Not in stock OpenEMR;
  schema migration would be needed.

The policy YAML's `note_types` matcher (`substance_abuse`) targets
`form_clinical_notes.clinical_notes_type`, not pnotes. Adding such a
row requires a paired `forms` linkage that the loader-style overlay
deliberately avoids — `pnotes` is the simpler insert path. Title-prefix
coverage is sufficient for the MVP demo.

(See `docs/DEVIATIONS.md` for detail.)

### Search-target coverage

Hand-picked terms in the body text exercise the FULLTEXT MATCH path:

- `"shortness of breath"`, `"echocardiogram"` — pid=8, follow-up note
- `"admission"`, `"chest pain"`, `"follow-up"` — pid=8, hospital note
- `"prescription"`, `"medication"` — pid=4, phone-call note

A `search_notes` query for any of these should ground a citation
against the matching note row.

### Vitals trend shape

| Patient | Trend |
|---|---|
| pid=8 Eula | BP 145/92 → 128/80 over 12 months; weight 175.5 → 166.0 lb. Hypertension responding to treatment. |
| pid=4 Alena | BP 116/70 – 120/74 (stable); weight 144.0 – 146.5 lb (stable). Healthy baseline. |

All readings carry `user = 'agentforge-overlay'` so they can be
distinguished from the single Synthea-imported vital per patient.

## TODO (downstream subtasks)

- 50.5 — `scripts/validate_seed_data.sql` post-import audit
- 50.6 — Realign eval fixtures + finalize this doc
