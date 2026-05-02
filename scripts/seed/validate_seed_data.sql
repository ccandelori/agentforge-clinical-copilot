-- AgentForge seed-data validation — runs after `agentforge_demo_overlay.sql`
-- and confirms the seed pipeline (Task 50) produced data fit for the demo.
--
-- Each row of the result set represents one check:
--   check_     — short stable label (don't rename without updating wrappers)
--   violations — 0 = pass, >0 = fail (count of bad rows)
--   detail     — human-readable hint
--
-- Run via:
--   docker exec -i development-easy-mysql-1 \
--     mariadb -uopenemr -popenemr openemr \
--     < scripts/seed/validate_seed_data.sql
--
-- Or (with non-zero exit on any failure):
--   ./scripts/seed/validate_seed.sh
--
-- Honest framing: these checks validate "the seed loaded correctly and
-- produced data the demo can show". They do NOT validate clinical realism
-- (e.g., diabetes ↔ A1c co-occurrence). Synthea handles that level of
-- coherence at the population level; verifying it per-patient via SQL is
-- fragile across code-system variants (SNOMED in lists.diagnosis vs
-- ICD/CPT in procedure_order) and gives false negatives more often than
-- it catches real bugs.

-- ----------------------------------------------------------------------
-- Demo overlay loaded
-- ----------------------------------------------------------------------

SELECT
  'overlay_pnotes_loaded' AS check_,
  GREATEST(6 - COUNT(*), 0) AS violations,
  CONCAT('expect >=6 pnotes with user=agentforge-overlay; found ',
         COUNT(*)) AS detail
FROM pnotes WHERE user = 'agentforge-overlay';

SELECT
  'overlay_vitals_loaded' AS check_,
  GREATEST(10 - COUNT(*), 0) AS violations,
  CONCAT('expect >=10 form_vitals with user=agentforge-overlay; found ',
         COUNT(*)) AS detail
FROM form_vitals WHERE user = 'agentforge-overlay';

-- ----------------------------------------------------------------------
-- Demo patients (pid=4 Alena, pid=8 Eula) have the right shape
-- ----------------------------------------------------------------------

SELECT
  'demo_pid_4_overlay_notes' AS check_,
  GREATEST(2 - COUNT(*), 0) AS violations,
  CONCAT('expect >=2 overlay pnotes for pid=4; found ', COUNT(*)) AS detail
FROM pnotes WHERE pid = 4 AND user = 'agentforge-overlay';

SELECT
  'demo_pid_8_overlay_notes' AS check_,
  GREATEST(4 - COUNT(*), 0) AS violations,
  CONCAT('expect >=4 overlay pnotes for pid=8; found ', COUNT(*)) AS detail
FROM pnotes WHERE pid = 8 AND user = 'agentforge-overlay';

SELECT
  'demo_pid_8_sud_notes' AS check_,
  GREATEST(2 - COUNT(*), 0) AS violations,
  CONCAT(
    'expect >=2 SUD-prefixed pnotes on pid=8 (substance_abuse_cfr42 demo); ',
    'found ', COUNT(*)
  ) AS detail
FROM pnotes
WHERE pid = 8
  AND (title LIKE 'SUD:%' OR title LIKE 'Substance Abuse:%');

SELECT
  'demo_pid_4_overlay_vitals' AS check_,
  GREATEST(5 - COUNT(*), 0) AS violations,
  CONCAT('expect >=5 overlay vitals on pid=4; found ', COUNT(*)) AS detail
FROM form_vitals WHERE pid = 4 AND user = 'agentforge-overlay';

SELECT
  'demo_pid_8_overlay_vitals' AS check_,
  GREATEST(5 - COUNT(*), 0) AS violations,
  CONCAT('expect >=5 overlay vitals on pid=8; found ', COUNT(*)) AS detail
FROM form_vitals WHERE pid = 8 AND user = 'agentforge-overlay';

-- ----------------------------------------------------------------------
-- Coverage thresholds — Synthea seed populated the previously-empty
-- tables that gated 3 of 9 tools
-- ----------------------------------------------------------------------

SELECT
  'pnotes_total' AS check_,
  GREATEST(100 - COUNT(*), 0) AS violations,
  CONCAT('expect >=100 pnotes total (FHIR loader pre-overlay); found ',
         COUNT(*)) AS detail
FROM pnotes;

SELECT
  'recent_encounters' AS check_,
  GREATEST(20 - COUNT(*), 0) AS violations,
  CONCAT(
    'expect >=20 form_encounter rows in last 365 days; found ', COUNT(*)
  ) AS detail
FROM form_encounter WHERE date >= DATE_SUB(NOW(), INTERVAL 365 DAY);

SELECT
  'lab_results_total' AS check_,
  GREATEST(500 - COUNT(*), 0) AS violations,
  CONCAT('expect >=500 procedure_result rows; found ', COUNT(*)) AS detail
FROM procedure_result;

-- ----------------------------------------------------------------------
-- Hygiene
-- ----------------------------------------------------------------------

SELECT
  'no_null_encounter_dates' AS check_,
  COUNT(*) AS violations,
  CONCAT(COUNT(*), ' form_encounter rows with NULL date') AS detail
FROM form_encounter WHERE date IS NULL;

SELECT
  'no_orphaned_lists' AS check_,
  COUNT(*) AS violations,
  CONCAT(COUNT(*), ' lists rows whose pid is not in patient_data') AS detail
FROM lists l
LEFT JOIN patient_data p ON l.pid = p.pid
WHERE p.pid IS NULL;

SELECT
  'no_orphaned_pnotes' AS check_,
  COUNT(*) AS violations,
  CONCAT(COUNT(*), ' pnotes rows whose pid is not in patient_data') AS detail
FROM pnotes pn
LEFT JOIN patient_data p ON pn.pid = p.pid
WHERE p.pid IS NULL;

SELECT
  'no_orphaned_form_encounters' AS check_,
  COUNT(*) AS violations,
  CONCAT(
    COUNT(*), ' form_encounter rows whose pid is not in patient_data'
  ) AS detail
FROM form_encounter fe
LEFT JOIN patient_data p ON fe.pid = p.pid
WHERE p.pid IS NULL;
