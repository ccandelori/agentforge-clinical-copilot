# AUDIT.md — OpenEMR Pre-Integration Audit

**Project:** AgentForge Clinical Co-Pilot
**Audit date:** 2026-04-28
**Auditor:** Cameron Candelori
**Subject:** [openemr/openemr](https://github.com/openemr/openemr) at fork point, deployed at `https://<droplet>:9300` (DigitalOcean droplet, 4 GB RAM / 2 vCPU, `openemr/openemr:flex` development image with bundled demo data, self-signed TLS). The deployed instance and the local development environment now run the same image + data pair (D2).

---

## Executive Summary

OpenEMR is a mature open-source EHR with a deeply useful audit and authorization foundation but a data model that does not natively support the record-level access controls our user stories require. The audit found 36 specific issues across five domains; the seven below shape the integration plan.

**1. Audit logging is comprehensive but doubles as a PHI repository.** `EventAuditLogger` captures every patient-record access with timestamp, user, category, IP, and SHA3-512 tamper checksums — HIPAA-grade audit for free. But `api_log` stores full request and response bodies in plaintext (`src/RestControllers/Subscriber/ApiResponseLoggerListener.php:84-85`), so every agent API call would write demographics, problems, and clinical notes into a long-lived searchable table. Our observability posture is designed against this directly.

**2. Break-the-glass access is fully implemented.** `BreakglassChecker` plus the `gbl_force_log_breakglass` flag and a free-text reason on `log.comments` give us the exact workflow the 2 AM hospitalist needs. We inherit it; the agent UX captures the reason.

**3. Record-level sensitivity flags are NOT implemented.** GACL models only "normal" and "high" sensitivity at the role level (`src/Common/Acl/AclMain.php:67-69`); no schema fields exist on encounters, notes, or forms for 42 CFR Part 2, HIV, mental-health, or attending-only markers. The nurse-can't-see-psych-notes story cannot be enforced by OpenEMR. **Our agent implements this filter in its own gateway layer**, as an agent-owned concern.

**4. No service-principal auth — the agent runs in the user's session.** OAuth2 supports authorization-code and password grants only; no client-credentials grant exists. The agent inherits the authenticated clinician's ACL on every call — the correct security posture. The agent never accesses PHI on its own behalf; it borrows the clinician's authority for the duration of a turn.

**5. The latency budget is at risk without infrastructure changes.** No query-result cache, opt-in connection pooling, and several agent-relevant tables (`prescriptions`, `form_vitals`, `form_encounter`) lack composite indexes on `(pid, date DESC)` for the 90-day windows we need. Our 4s tool-phase budget assumes parallel queries against a warm DB; default config does not hit it. Redis plus three composite indexes is non-negotiable before production.

**6. Schema portability is broken across the project's own Docker images.** `demo_5_0_0_5.sql` succeeds on `openemr/openemr:flex` but breaks the install on `openemr/openemr:latest` — the demo SQL recreates tables with an older column set, removing fields the production image's audit logger depends on (e.g., `log_comment_encrypt.checksum_api`). No supported way exists to seed the production image with the dev image's demo data. **The eval dataset is pinned to a fixed image+data pair**, captured and version-controlled.

**7. Default credentials and a hardcoded GitHub Composer token.** `admin/pass` is baked into `docker/production/docker-compose.yml`; a live GitHub Composer token sits in `docker/development-easy/docker-compose.yml:60`. The first is a critical deploy gate; the second is secret-in-source and has likely been exploited in the wild for fetching private dependencies. Rotation is a precondition for any production work.

The integration plan in ARCHITECTURE.md treats #1, #3, and #5 as load-bearing constraints, #2 as an enabling feature, and #4 as the auth model we adopt rather than fight.

---

## 1. Security

| # | Severity | Finding | File:line |
|---|---|---|---|
| S1 | Critical | API request and response bodies persisted in `api_log` in plaintext — full PHI in long-lived audit table. The "log everything OR log nothing" config is binary, breaking SOC2 + HIPAA balance. | `src/RestControllers/Subscriber/ApiResponseLoggerListener.php:58-85` |
| S2 | High | GACL has no per-record sensitivity model. Cannot enforce 42 CFR Part 2, HIV, mental-health, or attending-only filtering at the data layer. | `src/Common/Acl/AclMain.php:67-69` |
| S3 | High | Default credentials (`admin`/`pass`) baked into production docker-compose env. | `docker/production/docker-compose.yml` |
| S4 | High | Live GitHub Composer token committed to the development docker-compose. | `docker/development-easy/docker-compose.yml:60` |
| S5 | High | Hardcoded 7200-second session timeout with no per-role policy. Agent sessions could persist 2h post-auth. | `interface/globals.php:613`; `src/Common/Session/SessionTracker.php:58` |
| S6 | Medium | Session cookies default to `Secure => false`. On non-HTTPS deployments, tokens are MITM-interceptable. | `src/Common/Session/SessionConfigurationBuilder.php:26` |
| S7 | Medium | CSRF tokens are SHA-256 truncated to 40 chars to fit GET requests; no per-transaction nonces. | `src/Common/Csrf/CsrfUtils.php:55` |
| S8 | Low | SQL error logger concatenates raw query text into messages; sanitized but invites PHI leakage if a binding accidentally appears. | `library/sql.inc.php:192-194` |
| S9 | Medium | MVP demo URL deploys the full development stack (`openemr/openemr:flex` + couchdb + openldap + selenium + mailpit + phpmyadmin), exposing services not required for the agent and increasing attack surface. Acceptable for demo with no real PHI; must not propagate to production. | `docker/development-easy/docker-compose.yml`; deployed at `https://<droplet>:9300` |

**Agent implications:** the gateway layer must re-implement record-level sensitivity (S2). The `api_log` PHI exposure (S1) means our observability stack must NOT mirror OpenEMR's audit log into Langfuse — we log metadata only. Default-creds rotation and Composer-token revocation are pre-deploy hard gates.

---

## 2. Performance

| # | Severity | Finding | Reference |
|---|---|---|---|
| P1 | Critical | No query-result cache layer. Every patient-summary call re-executes the same 7–9 queries. Baseline ~500ms × 9 = 4.5s — already over the 4s tool-phase budget. | `src/Common/Database/QueryUtils.php`; `src/BC/DatabaseConnectionFactory.php` |
| P2 | High | Missing composite indexes for time-range queries. `prescriptions` indexes `patient_id` but not `(patient_id, active, date)`. `form_vitals` indexes `pid` only, not `(pid, date)`. `form_encounter` has `(pid, encounter)` but not `(pid, date DESC)`. | `sql/database.sql:2060,2449,8749` |
| P3 | High | N+1 patterns in `AllergyIntoleranceService` (joins then `while sqlFetchArray` row-by-row hydration) and `ObservationLabService` (5+ LEFT JOINs per query). | `src/Services/AllergyIntoleranceService.php:113`; `src/Services/ObservationLabService.php:80-140` |
| P4 | Medium | DB connection pooling is opt-in via `$enable_database_connection_pooling`. Default config opens a fresh connection per request. | `src/BC/DatabaseConnectionFactory.php:140-150` |
| P5 | Medium | `/interface/main/tabs/main.php` runs ProductRegistrationService, logo services, and ACL setup on every page load (~100–200ms). Agent must avoid the HTML interface and call APIs. | `interface/main/tabs/main.php` (572 lines) |
| P6 | Medium | `QueryUtils` uses ADODB-style mysqli with per-call binding; prepared statements are not reused across the request. ~50–100ms overhead/query vs cached prepared statements. | `src/Common/Database/QueryUtils.php:83` |

**Agent implications:** pre-deploy infrastructure work is mandatory — Redis cache (60–90s TTL keyed on `(user_id, patient_id, query_kind)`), three composite indexes on the tables above, connection pooling enabled. Without these, we miss p95. Prefer FHIR (`/apis/fhir/r4/`) over direct SQL — the P3 join overhead happens once server-side per resource type, and FHIR responses batch.

---

## 3. Architecture

OpenEMR is a hybrid: 1,942 modern PSR-4 PHP files in `src/` (the `OpenEMR\` namespace, ~24 MB) and 596 procedural legacy files in `library/` (~8 MB). Patient data access splits across both layers. For our agent, the modern layer is sufficient and preferred.

| # | Finding | Reference |
|---|---|---|
| A1 | A clean Service layer exists. ~52 `BaseService` subclasses (`PatientService`, `EncounterService`, `ClinicalNotesService`, `AllergyIntoleranceService`, etc.) provide typed, EventDispatcher-aware access. **The agent reads exclusively through these.** | `src/Services/` |
| A2 | Patient lifecycle events fire via Symfony EventDispatcher: `BeforePatientCreatedEvent`, `PatientCreatedEvent`, `BeforePatientUpdatedEvent`, `PatientUpdatedEvent`. Encounter-level events are sparse. | `src/Events/Patient/` |
| A3 | FHIR R4 API (`/apis/fhir/r4/`) is the canonical external interface. The internal REST API (`/api/`) is non-standards. Agent should prefer FHIR for compatibility and built-in scope checks. | `src/RestControllers/FhirPatientRestController.php` |
| A4 | OAuth2 supports authorization-code + password grants only. **No client-credentials (service-principal) grant.** Every token represents a human user. The agent runs in the user's session context. | `src/Common/Auth/OpenIDConnect/Repositories/` |
| A5 | Module system is event-bus-only. A custom module can subscribe to events but cannot intercept REST calls or replace services. The agent UI plugs in as a module that registers a Twig card and listens to events. | `interface/modules/custom_modules/oe-module-dashboard-context/` |
| A6 | Patient summary view is server-rendered PHP (`interface/patient_file/summary/`, 36 procedural files) plus Twig card components (`templates/patient/card/`). No SPA. Agent chat panel is a Twig card + vanilla JS / jQuery 3.7. Avoid Angular 1.8 and React. | `templates/patient/card/`, `interface/patient_file/summary/` |
| A7 | Authorization is runtime-checked via `RestConfig::request_authorization_check()`, not type-system encoded. There are no scoped repositories or domain primitives for `UserId`/`FacilityId`. | `src/RestControllers/RestConfig.php` |
| A8 | Two parallel database access surfaces: `QueryUtils` (legacy ADODB) and Doctrine DBAL. No ORM; entity rows are untyped arrays mapped manually inside services. | `src/Common/Database/QueryUtils.php` |

**Agent implications:** the integration shape is concrete — the agent is an OpenEMR custom module that reads via `BaseService` (or FHIR R4 for cross-cutting), authenticates as the logged-in clinician, and renders as a Twig card on the patient summary page. Orchestration and the LLM call live in a sidecar the module talks to over HTTPS.

---

## 4. Data Quality

| # | Severity | Finding | Reference |
|---|---|---|---|
| D1 | Critical | Two parallel migration systems coexist: legacy `sql_upgrade.php` running scripts from `sql/*.sql`, and a new Doctrine Migrations framework in `db/Migrations/`. The Doctrine path enforces `utf8mb4_general_ci`; the legacy path does not. Charset can drift across tables. | `sql_upgrade.php`; `db/migration-config.php`; `src/Core/Migrations/CreateTableTrait.php` |
| D2 | Critical | The bundled `demo_5_0_0_5.sql` is incompatible with the current `openemr/openemr:latest` schema. Importing it drops tables and removes newer columns (e.g., `log_comment_encrypt.checksum_api`), breaking the production image's audit logger. **No portable demo seed across the project's own images.** | `/root/demo_5_0_0_5.sql` (in flex image) vs current `sql/database.sql` |
| D3 | High | Medications use soft-delete via `prescriptions.active` flag. Discontinued meds remain in the table indefinitely with no `deleted_date`. | `sql/database.sql` (prescriptions) |
| D4 | High | Date fields mix `NULL` and zero-date sentinel `'0000-00-00 00:00:00'`. Legacy upgrade scripts reference conversion of zero-dates to empty string. Comparisons like `deceased_date > NOW()` will misbehave. | `sql/database.sql`; `sql/2_7_2-to-2_7_3_upgrade.sql` |
| D5 | Medium | Diagnoses are stored in three places with no normalized FK: `lists.diagnosis` (free text), `prescriptions.diagnosis` (free text), and `list_options.codes` (pipe-delimited `'ICD10:I10'`). | `sql/database.sql` |
| D6 | Medium | Patient identity is fragmented: `id`, `pid`, `uuid`, `pubpid`. Documentation does not specify which is canonical. `pid` and `id` appear 1:1 but it is not enforced. | `sql/database.sql` (patient_data) |
| D7 | Medium | Upgrade scripts `DELETE FROM list_options WHERE list_id = 'X'` rather than insert-on-conflict. Admin customizations in those `list_id`s are silently erased. | `sql/6_1_0-to-7_0_0_upgrade.sql`; `sql/6_0_0-to-6_1_0_upgrade.sql` |
| D8 | Medium | Most `patient_data` columns are `NULL DEFAULT` without `NOT NULL` (`DOB`, `occupation`, `interpreter_needed`, `care_team_provider`, etc.). Cannot distinguish "unknown" from "blank" from "missing." | `sql/database.sql` (patient_data) |

**Agent implications (locks the verifier design):**
- "Not on file" must be a first-class response (D8) — already locked in the v1 verification rules.
- Active filtering on `prescriptions.active = 1` is mandatory (D3) — encoded in the medication tool contract.
- Patient ID normalization happens at the gateway layer; internal queries use `pid`, external citations use `pubpid`/UUID (D6).
- Free-text diagnosis fields cannot be JOINed by code; the agent surfaces the text verbatim and notes when a structured code exists (D5).
- The eval dataset is captured against a single pinned `(image_sha, demo_data_sha)` pair (D2).
- Charset-aware string handling required (D1).

---

## 5. Compliance & Regulatory (HIPAA-focused)

| # | Severity | Finding | Reference |
|---|---|---|---|
| C1 | Strong (positive) | Comprehensive patient-record access logging via `EventAuditLogger`. `audit_master` and `log` tables capture timestamp, user, category, IP, success flag, and SHA3-512 tamper checksums. **The agent inherits HIPAA-grade access logging by routing through `BaseService`.** | `src/Common/Logging/EventAuditLogger.php` |
| C2 | Strong (positive) | Break-the-glass access is implemented end-to-end. `BreakglassChecker::isBreakglassUser()`, `gbl_force_log_breakglass` flag, reason capture in `log.comments`. Hospitalist 2 AM use case is supported. | `src/Common/Logging/BreakglassChecker.php` |
| C3 | High (gap) | Encryption at rest is selective. Only audit comments are encrypted (`log_comment_encrypt`, with a Yes/No enum + checksum). Patient names, SSN, DOB, encounter notes are plaintext in MariaDB. | `src/Common/Crypto/CryptoInterface.php`; `log_comment_encrypt` table |
| C4 | High (gap) | No record-level sensitivity flags in the schema. Cannot mark individual notes/encounters as 42 CFR Part 2, HIV, attending-only, or psych-restricted. (Same finding as S2.) | `forms/`, `encounter`, `pnotes` tables |
| C5 | High (positive→risk) | `api_log` table captures `request_url`, `request_body`, `response` (longtext), `user_id`, `patient_id`, IP, `created_time` — full end-to-end API audit trail for free, but the table itself becomes a PHI store with no encryption or retention policy. | `api_log` schema (`sql/database.sql`) |
| C6 | Medium (gap) | No automatic retention or purge policy on `log`, `log_comment_encrypt`, or `api_log` tables. Manual backup exists in `interface/main/backup.php`. HIPAA's six-year minimum is not enforced or capped. | repository-wide search |
| C7 | Medium | Patient consent flags exist but are limited: `allow_patient_portal`, `hipaa_mail`, `hipaa_voice`, `hipaa_allowsms`, `hipaa_allowemail`. **No consent flag for AI/research use.** | `sql/database.sql` (patient_data) |
| C8 | Medium | DEBUG-level logging via `error_log()` writes to syslog/php.log without filtering patient identifiers. Exception stack traces in clinical paths can leak `$patientId` and demographics into Apache/PHP logs. | `src/Common/Logging/SystemLogger.php` |

**Agent implications:**
- Routing reads through `BaseService` gives us HIPAA audit logging for free (C1) — confirmed integration approach.
- `BreakglassChecker` is the lever for the hospitalist 2 AM use case (C2). The agent calls it before any access outside the user's normal panel and writes a structured reason to `log.comments`.
- Sensitivity filtering is an agent-layer concern (C4) — locked into our verification design's domain constraint #3.
- The `api_log` PHI exposure (C5) is a load-bearing reason our **observability stack stores metadata only** — already locked in the presearch.
- `gbl_oe_log_purge` or equivalent retention policy must be defined and documented in our deployment guide (C6).
- We add an explicit `allow_ai_analysis` consent flag at the gateway layer or treat `allow_patient_portal` as a proxy until product decides (C7).
- Agent error-handling logs sanitized error codes only; `$patient_id` and `$patient_name` never reach `error_log()` (C8).

---

## Methodology

The audit was performed by:
- Reading project documentation: `CLAUDE.md`, `CONTRIBUTING.md`, `API_README.md`, `FHIR_README.md`.
- Static codebase exploration via grep, file enumeration, and targeted reads across `/src`, `/library`, `/interface`, `/sql`, `/apis`, `/templates`, and `/docker`.
- Local OpenEMR run on `docker/development-easy/` with `dev-reset-install-demodata`, and verification of demo patients (Phil Belford, Susan Underwood).
- Public deployment to a DigitalOcean droplet. **Initial deploy used `docker/production/docker-compose.yml` (`openemr/openemr:latest`); the schema-portability finding (D2) was discovered firsthand when the demo SQL broke the production image's install.** The deployed instance was subsequently switched to `openemr/openemr:flex` (the development image) so the deployed environment matches the local development environment and the eval dataset's pinned image+data pair. This is acceptable for a demo URL; production deployment requires a custom image built from the fork (see ARCHITECTURE.md §10–§11).
- Five parallel domain-focused review passes across security, performance, architecture, data quality, and compliance.

**Trade-off accepted on the deployed demo URL:** the flex image runs the full development stack (couchdb, openldap, mailpit, selenium, phpmyadmin) alongside the EHR, increasing the attack surface beyond what a production deployment would expose. This is acceptable for a demo URL with no real PHI; it is documented as a finding (call it S9: "MVP demo URL exposes development services not required for the agent") and must not propagate to production.

The audit did **not** perform: dynamic security testing (no SQLi/XSS fuzzing), no penetration testing, no live SQL `EXPLAIN` analysis on the deployed instance, and no review of OpenEMR's third-party dependencies. These are the natural extensions for a follow-on hardening pass before a real production deployment.

---

## Findings index

**Critical (4):** S1 (api_log PHI), P1 (no query cache), D1 (dual migration systems), D2 (no portable demo seed).

**High (10):** S2 (no record-level sensitivity), S3 (default creds), S4 (Composer token), S5 (session timeout), P2 (missing indexes), P3 (N+1 in services), D3 (medication soft-delete), D4 (zero-date handling), C3 (selective encryption), C4 (no sensitivity flags), C5 (api_log = PHI store).

**Medium (14):** S6 (insecure cookies default), S7 (truncated CSRF), S9 (dev stack on demo URL), P4 (connection pooling opt-in), P5 (HTML interface overhead), P6 (no prepared-statement reuse), A4 (no service principal), A8 (dual DB access surfaces), D5 (free-text diagnoses), D6 (fragmented patient ID), D7 (list_options upgrade clobber), D8 (NULL-default everywhere), C6 (no audit retention), C7 (limited consent), C8 (PHI in error logs).

**Low (1):** S8 (raw SQL in error log).

**Strong/positive (3):** C1 (audit logging), C2 (breakglass), A1 (clean Service layer).
