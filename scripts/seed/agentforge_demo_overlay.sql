-- AgentForge demo overlay — hand-crafted notes + vitals trend for the
-- two designated demo patients (pid=8 Eula Crist, pid=4 Alena Marquardt).
--
-- Loads predictable note + vitals data on top of the Synthea seed so the
-- demo can:
--   1. Show a real vitals trend (Synthea's CCDA importer collapses to 1
--      reading/patient — see docs/test-data.md, subtask 50.3).
--   2. Exercise the substance_abuse_cfr42 sensitivity rule via the
--      `note_title_prefixes` matcher ("SUD:" / "Substance Abuse:") —
--      Synthea's templated notes never carry these prefixes naturally.
--   3. Exercise the search_notes FULLTEXT MATCH on hand-picked terms
--      ("shortness of breath", "admission", "follow-up", "chest pain").
--
-- Run inside the openemr mysql container via:
--   docker exec -i development-easy-mysql-1 mariadb -uopenemr -popenemr openemr \
--     < scripts/seed/agentforge_demo_overlay.sql
--
-- Idempotent: every row carries user = 'agentforge-overlay', and the file
-- DELETEs all such rows up-front before re-inserting.

-- ----------------------------------------------------------------------
-- 1. Idempotency — wipe any prior overlay before re-loading
-- ----------------------------------------------------------------------

DELETE FROM pnotes      WHERE user = 'agentforge-overlay';
DELETE FROM form_vitals WHERE user = 'agentforge-overlay';

-- ----------------------------------------------------------------------
-- 2. Hand-crafted pnotes
-- ----------------------------------------------------------------------

-- pid=8 (Eula Crist, complex chronic) — 4 notes
--
-- Coverage: standard, two SUD-gated (one per title prefix variant), one
-- search-target.

INSERT INTO pnotes (date, body, pid, user, groupname, activity, authorized,
                    title, message_status)
VALUES
  -- Standard recent note. Searchable terms: shortness of breath,
  -- follow-up, echocardiogram.
  (DATE_SUB(NOW(), INTERVAL 30 DAY),
   'Patient seen for follow-up after recent emergency department visit. Reports continued shortness of breath on exertion, especially when climbing stairs. No chest pain, no syncope. Lung sounds clear bilaterally. Plan: schedule echocardiogram to evaluate cardiac function, review medication adherence with clinical pharmacist, follow-up in 4 weeks.',
   8, 'agentforge-overlay', 'Default', 1, 1,
   'Follow-up: Cardiology consult', 'New'),

  -- SUD-gated via "SUD:" title prefix. Should be hidden behind
  -- substance_abuse_cfr42 unless the user has cfr42_authorized.
  (DATE_SUB(NOW(), INTERVAL 60 DAY),
   'Week 3 of 12-week intensive outpatient program (IOP). Patient reports stable abstinence since program admission. Mood improved, sleep regulating. Continues to engage well in group therapy. Plan: continue weekly individual sessions, next group meeting Thursday.',
   8, 'agentforge-overlay', 'Default', 1, 1,
   'SUD: Outpatient counseling session', 'New'),

  -- SUD-gated via "Substance Abuse:" prefix variant.
  (DATE_SUB(NOW(), INTERVAL 90 DAY),
   'Initial intake assessment completed. Patient referred from PCP. Reports daily alcohol use over past 18 months, motivated for change. AUDIT score 14. No prior treatment history. Recommendation: enroll in 12-week outpatient program, weekly individual + group sessions.',
   8, 'agentforge-overlay', 'Default', 1, 1,
   'Substance Abuse: Initial intake', 'New'),

  -- Search-target note. Searchable terms: admission, chest pain,
  -- follow-up.
  (DATE_SUB(NOW(), INTERVAL 14 DAY),
   'Patient admitted overnight for evaluation of chest pain. ECG showed nonspecific ST changes; serial troponins negative. Stress test deferred pending outpatient cardiology consult. Discharged after 24-hour observation. Diagnosis: non-cardiac chest pain, likely musculoskeletal. Follow-up scheduled with PCP within one week.',
   8, 'agentforge-overlay', 'Default', 1, 1,
   'Hospital admission summary', 'New');

-- pid=4 (Alena Marquardt, sparse) — 2 notes

INSERT INTO pnotes (date, body, pid, user, groupname, activity, authorized,
                    title, message_status)
VALUES
  -- Standard recent note. Searchable: routine, annual.
  (DATE_SUB(NOW(), INTERVAL 60 DAY),
   'Routine annual physical examination. Patient reports no concerns, denies any new symptoms. Vital signs stable, weight unchanged from prior visit. Recommended age-appropriate screenings: pap smear due, mammogram in 2 years per current guidelines. No medication changes needed.',
   4, 'agentforge-overlay', 'Default', 1, 1,
   'Annual physical exam', 'New'),

  -- Phone-call note with searchable terms: medication, prescription.
  (DATE_SUB(NOW(), INTERVAL 21 DAY),
   'Patient called nurse line regarding new prescription. Clarified dosing schedule (once daily with food, evening). Patient reports no adverse effects since starting last week. No additional questions. Documented; no MD action required.',
   4, 'agentforge-overlay', 'Default', 1, 1,
   'Phone call: medication question', 'New');

-- ----------------------------------------------------------------------
-- 3. Hand-crafted vitals trends
-- ----------------------------------------------------------------------

-- pid=8 (Eula, 53F): hypertension under treatment — BP / weight trend
-- improves over 12 months. Height held constant at 66 in (5'6").

INSERT INTO form_vitals (date, pid, user, groupname, activity, authorized,
                         bps, bpd, height, weight, pulse, respiration,
                         temperature, BMI)
VALUES
  (DATE_SUB(NOW(), INTERVAL 365 DAY), 8, 'agentforge-overlay', 'Default', 1, 1,
   '145', '92', 66.0, 175.5, 78, 16, 98.4, 28.3),
  (DATE_SUB(NOW(), INTERVAL 270 DAY), 8, 'agentforge-overlay', 'Default', 1, 1,
   '140', '88', 66.0, 173.0, 76, 15, 98.6, 27.9),
  (DATE_SUB(NOW(), INTERVAL 180 DAY), 8, 'agentforge-overlay', 'Default', 1, 1,
   '138', '86', 66.0, 171.0, 74, 14, 98.5, 27.6),
  (DATE_SUB(NOW(), INTERVAL  90 DAY), 8, 'agentforge-overlay', 'Default', 1, 1,
   '132', '82', 66.0, 168.5, 72, 14, 98.6, 27.2),
  (DATE_SUB(NOW(), INTERVAL  30 DAY), 8, 'agentforge-overlay', 'Default', 1, 1,
   '128', '80', 66.0, 166.0, 70, 14, 98.4, 26.8);

-- pid=4 (Alena, 35F): healthy baseline, stable over 12 months. Height
-- held constant at 64 in (5'4").

INSERT INTO form_vitals (date, pid, user, groupname, activity, authorized,
                         bps, bpd, height, weight, pulse, respiration,
                         temperature, BMI)
VALUES
  (DATE_SUB(NOW(), INTERVAL 300 DAY), 4, 'agentforge-overlay', 'Default', 1, 1,
   '118', '72', 64.0, 145.0, 68, 14, 98.6, 24.9),
  (DATE_SUB(NOW(), INTERVAL 200 DAY), 4, 'agentforge-overlay', 'Default', 1, 1,
   '120', '74', 64.0, 146.5, 70, 14, 98.4, 25.2),
  (DATE_SUB(NOW(), INTERVAL 100 DAY), 4, 'agentforge-overlay', 'Default', 1, 1,
   '116', '70', 64.0, 144.0, 66, 14, 98.5, 24.7),
  (DATE_SUB(NOW(), INTERVAL  50 DAY), 4, 'agentforge-overlay', 'Default', 1, 1,
   '119', '73', 64.0, 145.5, 68, 14, 98.6, 25.0),
  (DATE_SUB(NOW(), INTERVAL   7 DAY), 4, 'agentforge-overlay', 'Default', 1, 1,
   '117', '72', 64.0, 144.5, 67, 14, 98.5, 24.8);

-- ----------------------------------------------------------------------
-- 4. Quick verify
-- ----------------------------------------------------------------------

SELECT 'pnotes overlay inserted'      AS check_, COUNT(*) AS rows_ FROM pnotes      WHERE user = 'agentforge-overlay';
SELECT 'form_vitals overlay inserted' AS check_, COUNT(*) AS rows_ FROM form_vitals WHERE user = 'agentforge-overlay';
