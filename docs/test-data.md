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

## TODO (downstream subtasks)

- 50.3 — Generate ~20 patients, batch import. **Extended scope:**
  also wire a FHIR DocumentReference loader (option a or b above).
- 50.4 — Hand-crafted SQL note overlay for designated demo patients
- 50.5 — `scripts/validate_seed_data.sql` post-import audit
- 50.6 — Realign eval fixtures + finalize this doc
