-- Care team seed for the four W2 demo personas.
--
-- Synthea-imported demo personas land in OpenEMR with empty
-- `care_teams` and `care_team_member` tables — Synthea ingestion does
-- not preserve CareTeam resources. The dashboard's `CareTeamCard.vue`
-- has nothing to render against those personas without this seed.
--
-- Personas (matched by `patient_data.pubpid`, not by `pid`, since pids
-- vary by environment):
--   * Margaret Chen     (pubpid MRN-2026-04481)   → Chen Care Team
--   * James Whitaker    (pubpid MRN-2026-04492)   → Whitaker Care Team
--   * Sofia Reyes       (pubpid MRN-2026-DEMO-03) → Reyes Care Team
--   * Robert Kowalski   (pubpid MRN-2026-DEMO-04) → Kowalski Care Team
--
-- Idempotency. Every row carries `team_name LIKE '% Care Team'`. The
-- script DELETEs all such rows up-front, then re-inserts. Running it
-- twice produces the same row set, no duplicates.
--
-- Member backing. Each member row uses `user_id` (not `contact_id`) and
-- references rows that exist in every dev-easy / droplet OpenEMR
-- install: `users.id IN (1, 5, 6)` for `admin`, `clinician`, `physician`
-- respectively. Using `users` keeps the FHIR projection's
-- `getCareTeamProviders` JOIN happy without inserting `contact` /
-- `person` rows just to feed the demo.
--
-- Roles are the canonical option_ids from
-- `list_options WHERE list_id = 'care_team_roles'`:
--   physician, nurse_practitioner, case_manager, social_worker.
-- (Verified against dev-easy at seed time; same set ships in
-- `sql/database.sql` for fresh installs.)
--
-- Run on dev-easy:
--   docker exec -i development-easy-mysql-1 \
--     mariadb -uopenemr -popenemr openemr < scripts/seed/care_team.sql
--
-- Run on the droplet (from the host, after `scp`-ing this file in):
--   docker exec -i development-easy-mysql-1 \
--     mariadb -uopenemr -popenemr openemr < /tmp/care_team.sql

-- ----------------------------------------------------------------------
-- 1. Idempotency — wipe any prior demo seed before re-loading
-- ----------------------------------------------------------------------

-- Delete child rows first (FK shape is loose — care_team_member has no
-- enforced FK to care_teams in OpenEMR's schema — but order-of-ops
-- matters for the lookup we do here).
DELETE ctm FROM care_team_member ctm
INNER JOIN care_teams ct ON ctm.care_team_id = ct.id
WHERE ct.team_name IN (
    'Chen Care Team',
    'Whitaker Care Team',
    'Reyes Care Team',
    'Kowalski Care Team'
);

DELETE FROM care_teams
WHERE team_name IN (
    'Chen Care Team',
    'Whitaker Care Team',
    'Reyes Care Team',
    'Kowalski Care Team'
);

-- ----------------------------------------------------------------------
-- 2. Insert one care_teams row per persona
-- ----------------------------------------------------------------------
--
-- `INSERT ... SELECT` skips rows whose persona isn't seeded — keeping
-- the script safe to run on environments where only a subset of demo
-- personas exist (e.g. an OpenEMR install with just Chen).

INSERT INTO care_teams (uuid, pid, status, team_name)
SELECT UNHEX(REPLACE(UUID(), '-', '')), pid, 'active', 'Chen Care Team'
FROM patient_data WHERE pubpid = 'MRN-2026-04481';

INSERT INTO care_teams (uuid, pid, status, team_name)
SELECT UNHEX(REPLACE(UUID(), '-', '')), pid, 'active', 'Whitaker Care Team'
FROM patient_data WHERE pubpid = 'MRN-2026-04492';

INSERT INTO care_teams (uuid, pid, status, team_name)
SELECT UNHEX(REPLACE(UUID(), '-', '')), pid, 'active', 'Reyes Care Team'
FROM patient_data WHERE pubpid = 'MRN-2026-DEMO-03';

INSERT INTO care_teams (uuid, pid, status, team_name)
SELECT UNHEX(REPLACE(UUID(), '-', '')), pid, 'active', 'Kowalski Care Team'
FROM patient_data WHERE pubpid = 'MRN-2026-DEMO-04';

-- ----------------------------------------------------------------------
-- 3. Insert care_team_member rows — three per team
-- ----------------------------------------------------------------------
--
-- Same `INSERT ... SELECT` shape, joining the freshly-created
-- `care_teams` row by `team_name`. We don't depend on AUTO_INCREMENT
-- snapshots, so each member insert is independent and safely re-runnable.

-- Chen Care Team — Physician + Nurse Practitioner + Case Manager
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 6, 'physician', 'active' FROM care_teams WHERE team_name = 'Chen Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 5, 'nurse_practitioner', 'active' FROM care_teams WHERE team_name = 'Chen Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 1, 'case_manager', 'active' FROM care_teams WHERE team_name = 'Chen Care Team';

-- Whitaker Care Team — Physician + Social Worker + Nurse Practitioner
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 6, 'physician', 'active' FROM care_teams WHERE team_name = 'Whitaker Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 1, 'social_worker', 'active' FROM care_teams WHERE team_name = 'Whitaker Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 5, 'nurse_practitioner', 'active' FROM care_teams WHERE team_name = 'Whitaker Care Team';

-- Reyes Care Team — Physician + Case Manager
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 6, 'physician', 'active' FROM care_teams WHERE team_name = 'Reyes Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 1, 'case_manager', 'active' FROM care_teams WHERE team_name = 'Reyes Care Team';

-- Kowalski Care Team — Physician + Nurse Practitioner
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 6, 'physician', 'active' FROM care_teams WHERE team_name = 'Kowalski Care Team';
INSERT INTO care_team_member (care_team_id, user_id, role, status)
SELECT id, 5, 'nurse_practitioner', 'active' FROM care_teams WHERE team_name = 'Kowalski Care Team';

-- ----------------------------------------------------------------------
-- 4. Backfill NPI on the seed users so they're discoverable via FHIR
-- ----------------------------------------------------------------------
--
-- OpenEMR's `PractitionerService::search()` (line ~88) gates the
-- entire Practitioner FHIR projection on a non-empty `users.npi`:
--   "the only thing that differentiates users as practitioners is our
--    npi number"
-- Without an NPI, `GET /apis/default/fhir/Practitioner/{user_uuid}`
-- returns 404 even though the user exists, the OAuth scope is
-- granted, and the CareTeam.participant.member reference points at
-- a real user UUID. The dashboard's CareTeam card resolves member
-- names by fetching `Practitioner/{id}` per participant; without an
-- NPI those fetches all 404 and the card renders "Unknown member"
-- per role.
--
-- Setting fake but well-formed test NPIs unblocks the whole chain.
-- Conditional WHERE keeps the update idempotent and refuses to
-- clobber a real NPI on a production install that happens to share
-- these user ids.

UPDATE users SET npi = '1003000423'
WHERE id = 1 AND (npi IS NULL OR npi = '');
UPDATE users SET npi = '1003000431'
WHERE id = 5 AND (npi IS NULL OR npi = '');
UPDATE users SET npi = '1003000449'
WHERE id = 6 AND (npi IS NULL OR npi = '');
