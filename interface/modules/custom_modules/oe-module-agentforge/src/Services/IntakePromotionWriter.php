<?php

/**
 * IntakePromotionWriter — clinician-approved write path that promotes
 * AI-extracted intake-form items into OpenEMR's structured EHR tables.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

/**
 * Writes one row per accepted item to the appropriate destination
 * table:
 *
 *  - allergies      → ``lists`` with ``type='allergy'``
 *  - problems       → ``lists`` with ``type='medical_problem'``
 *  - family history → ``lists`` with ``type='family_history'``
 *  - medications    → ``prescriptions`` (NOT ``lists``; see medication
 *    routing note below)
 *
 * The whole batch is wrapped in a DBAL transaction. If any single
 * insert fails the cascade rolls back so a half-applied promotion
 * doesn't leave the chart in a half-extracted state — that matters
 * for the safety story (clinician approves a set; they get the whole
 * set or nothing).
 *
 * Mapping decisions worth knowing:
 *
 * - **No facility / encounter linkage.** ``lists.activity = 1``
 *   marks the row active; ``lists.date = NOW()`` records when the
 *   clinician committed it. The rows are not anchored to an
 *   encounter (``encounter_id`` is not on the ``lists`` schema) —
 *   the chart-summary cards filter by ``pid`` + ``activity = 1`` so
 *   non-encounter rows still surface.
 * - **No clinician user.** ``lists.user`` would normally carry
 *   ``$_SESSION['authUser']`` but internal endpoints don't have a
 *   PHP session. We pass the JWT's looked-up username through so
 *   the audit row reflects who actually clicked Commit.
 * - **`comments` carries the source attribution.** Each row is
 *   tagged "Imported from AgentForge intake form (qr_id=…, doc_id=…)"
 *   so a clinician browsing the chart can trace any AI-promoted row
 *   back to the source extraction without leaving the chart UI.
 *
 * Allergy-specific field population (the load-bearing fix in
 * `fix(promote): populate canonical lists fields …`):
 *
 *   The dashboard's ``AllergiesCard`` reads from FHIR
 *   ``AllergyIntolerance``, which projects from canonical ``lists``
 *   columns the original writer left empty:
 *
 *   - ``lists.diagnosis`` → FHIR ``code.text`` / ``code.coding[0].display``
 *     → frontend ``substance``. Without this populated, the FHIR
 *     resource gets ``data-absent-unknown`` and the dashboard renders
 *     the substance as literally "Unknown", which the frontend's
 *     keyword-based category classifier then mis-buckets as
 *     "environmental" because "Unknown" doesn't match any drug or
 *     food keyword. Setting ``diagnosis = title`` (the substance
 *     text) fixes both the substance label AND the category badge in
 *     one shot.
 *   - ``lists.severity_al`` → FHIR ``criticality`` (low/high) →
 *     frontend ``severity``. The LLM extraction's free-form severity
 *     is normalized into the three buckets that round-trip cleanly
 *     through the FHIR criticality mapping at
 *     :php:class:`OpenEMR\\Services\\FHIR\\FhirAllergyIntoleranceService`
 *     (mild → low → mild; severe → high → severe; everything else
 *     intentionally left null so the frontend defaults to "moderate"
 *     rather than landing on the wrong bucket).
 *   - ``lists.reaction`` is intentionally left at the schema default
 *     of ``''`` — that column expects a ``list_options.option_id``
 *     (joined to ``list_id='reaction'``), and writing free text
 *     would either fail the FHIR projection's ``foreach`` over
 *     ``$dataRecord['reaction']`` or silently drop. The reaction
 *     text the panel collected is preserved in ``lists.comments``
 *     via the existing ``details`` channel, so a clinician browsing
 *     the chart row directly still sees it.
 *   - ``lists.subtype`` is left empty — the FHIR projection
 *     hardcodes ``category = "medication"`` and never reads the
 *     ``subtype`` column, and the frontend re-classifies the
 *     category from the substance text via keyword matching.
 *
 * Medication routing (resolved in
 * `fix(promote): re-route medication promotion to prescriptions`):
 *
 *   ``type='medication'`` rows in ``lists`` do NOT surface in the
 *   dashboard's ``MedicationsCard`` — that card reads from FHIR
 *   ``MedicationRequest``, which is projected by
 *   :php:class:`OpenEMR\\Services\\FHIR\\FhirMedicationRequestService`
 *   from the ``prescriptions`` table (a different table entirely with
 *   ``binary(16)`` UUID + several ``NOT NULL`` columns). For
 *   ``kind='medication'`` items we therefore branch into
 *   :php:meth:`insertPrescriptionRow` and write a single row to
 *   ``prescriptions`` instead of ``lists`` — single source of truth
 *   for the medication card, no shadow row in ``lists`` to confuse
 *   the chart-summary listing. The same per-row clinician approval
 *   gate still applies (the controller validates kinds before we
 *   ever see them); only the destination table changes.
 *
 * What this writer does NOT do:
 *
 * - It does not validate item shape — the controller has parsed the
 *   request body and confirmed each accepted item is a non-empty
 *   string before calling here.
 * - It does not call into FHIR-aware services
 *   (``AllergyIntoleranceService``, ``ConditionService``). Those
 *   services depend on session-resident config and a
 *   ``UuidRegistry`` constructor that mutates global state; calling
 *   them from a JWT-auth'd internal endpoint would mix two
 *   incompatible auth pipelines. The ``lists``-table rows we write
 *   here are exactly what the dashboard's GET endpoints (which
 *   already drive ``AllergiesCard`` / ``ProblemListCard``) read
 *   back — so the cards will refresh and render the new rows as
 *   soon as the cache invalidates.
 *
 * The returned :class:`PromotionResult` carries one
 * ``PromotedItemHandle`` per inserted row (with the new ``lists.id``)
 * so the caller can audit per-row and surface row-level failures
 * back to the dashboard.
 */
readonly class IntakePromotionWriter
{
    public const TYPE_ALLERGY = 'allergy';
    public const TYPE_PROBLEM = 'medical_problem';
    public const TYPE_MEDICATION = 'medication';
    public const TYPE_FAMILY_HISTORY = 'family_history';

    /**
     * Severity buckets that round-trip cleanly through the FHIR
     * AllergyIntolerance ``criticality`` mapping back to the
     * frontend's three-value ``AllergySeverity`` enum.
     *
     * Anything outside this set (including ``null`` / empty) is left
     * unset in ``lists.severity_al`` so the frontend falls back to
     * "moderate" rather than picking up the wrong bucket via the
     * default-mapped chain.
     */
    private const ROUND_TRIP_SEVERITIES = ['mild', 'severe'];

    public function __construct(
        private Connection $connection,
    ) {
    }

    /**
     * @param list<PromotionItem> $items Accepted items, already parsed
     *     and scope-checked by the controller.
     *
     * @return PromotionResult
     */
    public function persist(
        int $patientId,
        string $username,
        ?string $questionnaireResponseId,
        ?int $documentId,
        array $items,
    ): PromotionResult {
        return $this->connection->transactional(
            fn (): PromotionResult => $this->insertCascade(
                $patientId,
                $username,
                $questionnaireResponseId,
                $documentId,
                $items,
            ),
        );
    }

    /**
     * @param list<PromotionItem> $items
     */
    private function insertCascade(
        int $patientId,
        string $username,
        ?string $questionnaireResponseId,
        ?int $documentId,
        array $items,
    ): PromotionResult {
        $handles = [];
        foreach ($items as $item) {
            $rowId = $this->insertItem(
                $patientId,
                $username,
                $questionnaireResponseId,
                $documentId,
                $item,
            );
            $handles[] = new PromotedItemHandle(
                kind: $item->kind,
                // For medication items this is the new ``prescriptions.id``
                // rather than ``lists.id`` — the field is stable shape,
                // the underlying table varies by kind. Documented on
                // PromotedItemHandle.
                listsId: $rowId,
                title: $item->title,
            );
        }
        return new PromotionResult($handles);
    }

    /**
     * Dispatches one accepted item to the right destination table:
     *
     * - ``kind == 'medication'`` → ``prescriptions`` (via
     *   :php:meth:`insertPrescriptionRow`) so the dashboard's
     *   FHIR-backed MedicationsCard actually surfaces it.
     * - ``kind == 'allergy'`` → ``lists`` with the canonical
     *   FHIR-projection columns populated.
     * - everything else → ``lists`` with the minimal shape.
     *
     * Returns the newly-assigned row id from whichever table received
     * the row, captured via ``lastInsertId()`` immediately after the
     * insert.
     */
    private function insertItem(
        int $patientId,
        string $username,
        ?string $questionnaireResponseId,
        ?int $documentId,
        PromotionItem $item,
    ): int {
        $comments = $this->buildComments($item, $questionnaireResponseId, $documentId);

        if ($item->kind === self::TYPE_MEDICATION) {
            $this->insertPrescriptionRow(
                $patientId,
                $username,
                $item,
                $comments,
            );
        } elseif ($item->kind === self::TYPE_ALLERGY) {
            // Allergies need extra canonical-column population so the
            // FHIR AllergyIntolerance projection can build a non-DAR
            // resource. Other kinds keep the original minimal-shape
            // insert that ProblemListCard / FamilyHistory already
            // render correctly.
            $this->insertAllergyRow(
                $patientId,
                $username,
                $item,
                $comments,
            );
        } else {
            $this->insertGenericListsRow(
                $patientId,
                $username,
                $item,
                $comments,
            );
        }

        $insertedId = (int) $this->connection->lastInsertId();
        if ($insertedId <= 0) {
            // Defensive — lastInsertId() returning 0 means the driver
            // lost the new row's id; we'd be returning a bogus handle.
            // Caller's transactional() will roll back when this throws.
            throw new \RuntimeException(
                'IntakePromotionWriter: lastInsertId returned non-positive',
            );
        }
        return $insertedId;
    }

    /**
     * Inserts a row into ``lists`` with ``type='allergy'`` and the
     * canonical FHIR-projection columns populated.
     *
     * - ``diagnosis`` carries the substance text (the panel's
     *   ``title``). The FHIR projection's :php:meth:`addCoding` parses
     *   this with :php:meth:`CodeTypesService::parseCode`; without a
     *   ``TYPE:CODE`` separator the result is ``code = title`` /
     *   ``code_type = null``, which lands in
     *   ``code.coding[0].display`` and ``code.text`` — both surfaces
     *   the frontend's ``pickCodeableText`` reads.
     * - ``severity_al`` is set only when the parsed severity is one
     *   of the round-trip-clean values (mild / severe). Anything
     *   else (including a missing severity) is left at the schema
     *   default of ``NULL`` so the frontend defaults to "moderate"
     *   via its ``criticality === undefined`` fallthrough — which is
     *   the right answer when severity is genuinely unknown rather
     *   than picking the wrong bucket via the indirect mapping.
     */
    private function insertAllergyRow(
        int $patientId,
        string $username,
        PromotionItem $item,
        string $comments,
    ): void {
        $severityAl = $this->normaliseSeverityFromDetails($item->details);

        // We use a list of ``(column, placeholder)`` tuples rather
        // than a fixed SQL string so the optional ``severity_al``
        // can be omitted entirely (preserving the schema default of
        // ``NULL``) when severity isn't round-trippable.
        $columns = [
            'date'      => 'NOW()',
            'pid'       => ':pid',
            'type'      => ':type',
            'title'     => ':title',
            'diagnosis' => ':diagnosis',
            'activity'  => '1',
            'comments'  => ':comments',
            'user'      => ':user',
            'groupname' => ':groupname',
        ];
        $bind = [
            'pid'       => $patientId,
            'type'      => self::TYPE_ALLERGY,
            'title'     => $item->title,
            // diagnosis = substance text. The FHIR projection treats
            // this as the source for ``code.text`` / coding.display.
            'diagnosis' => $item->title,
            'comments'  => $comments,
            'user'      => $username,
            'groupname' => '',
        ];
        if ($severityAl !== null) {
            $columns['severity_al'] = ':severity_al';
            $bind['severity_al'] = $severityAl;
        }

        $this->connection->executeStatement(
            'INSERT INTO lists ('
            . implode(', ', array_keys($columns))
            . ') VALUES ('
            . implode(', ', array_values($columns))
            . ')',
            $bind,
        );
    }

    /**
     * Original minimal-shape insert for non-allergy kinds
     * (medical_problem, medication, family_history). Preserved verbatim
     * so the ProblemListCard / FamilyHistory paths keep working
     * exactly as they did before the allergy fix.
     */
    private function insertGenericListsRow(
        int $patientId,
        string $username,
        PromotionItem $item,
        string $comments,
    ): void {
        $this->connection->executeStatement(
            'INSERT INTO lists '
            . '(date, pid, type, title, activity, comments, user, groupname) '
            . 'VALUES (NOW(), :pid, :type, :title, 1, :comments, :user, :groupname)',
            [
                'pid' => $patientId,
                'type' => $item->kind,
                'title' => $item->title,
                'comments' => $comments,
                'user' => $username,
                // ``groupname`` is required by the schema and legacy
                // ``addList()`` populates it from ``$_SESSION['authProvider']``.
                // Internal endpoints don't carry that; an empty string
                // is safe (no FK) and matches what dev-easy seeds
                // produce when no session group is set.
                'groupname' => '',
            ],
        );
    }

    /**
     * Inserts a row into ``prescriptions`` so the FHIR
     * ``MedicationRequest`` projection picks it up — that's what the
     * dashboard's :code:`MedicationsCard` reads from. Mirrors the
     * subset of columns Synthea-imported rows actually populate (see
     * the SELECT from a sample row in commit message), so the row
     * shape stays compatible with the rest of the EHR's medication
     * UI (legacy `interface/orders/list.php`, the prescription edit
     * screen, etc.).
     *
     * Mapping decisions worth knowing:
     *
     * - **`uuid`** is generated from :php:func:`random_bytes(16)` —
     *   the column is ``binary(16)``. We don't write to
     *   ``uuid_registry`` here; the FHIR projection's
     *   :php:meth:`PrescriptionService::__construct` calls
     *   ``UuidRegistry::createMissingUuidsForTables(['prescriptions'])``,
     *   which only fills NULL uuids and leaves ours alone. The row is
     *   addressable via ``combined_prescriptions.uuid`` in the FHIR
     *   union SELECT either way — no registry round-trip is required
     *   to render the medication.
     * - **`drug_dosage_instructions`** carries the full free-text sig
     *   ("10 mg PO daily"). FHIR projection at
     *   :php:meth:`FhirMedicationRequestService::populateDosageInstruction`
     *   prefers this column over ``dosage`` for the
     *   ``dosageInstruction[0].text`` slot, and the frontend's
     *   ``projectMedicationRequest`` falls back to that text for the
     *   rendered ``frequency`` line.
     * - **`dosage`** carries the parsed dose substring ("10 mg") for
     *   any legacy UI that reads the column directly. The FHIR text
     *   path uses ``drug_dosage_instructions`` first, so this is
     *   belt-and-braces; setting both keeps the legacy chart edit
     *   screen honest without conflicting with the dashboard render.
     *   Note that the FHIR projection's text-set guard is
     *   ``!is_numeric($dosageInstructions)`` — we always write a
     *   string with units so the guard never fires.
     * - **`interval` / `route`** stay NULL. Both columns are
     *   ``int(11)`` foreign keys to ``list_options`` (option_id of
     *   ``drug_interval`` / ``drug_route`` respectively). Mapping
     *   "PO daily" → an interval option_id requires a runtime lookup
     *   we don't currently have; the user-facing frequency text is
     *   preserved in ``drug_dosage_instructions`` so nothing is lost
     *   on the dashboard. Cleaner to leave both NULL than to invent a
     *   wrong FK that would mis-render in the legacy prescription
     *   edit form.
     * - **`active = 1` + `end_date = NULL`** → the SQL CASE in
     *   :php:meth:`PrescriptionService::getBaseSql` resolves to
     *   ``status = 'active'`` → FHIR
     *   ``MedicationRequest.status = active`` → frontend
     *   ``Medication.status = 'active'``. This is the load-bearing
     *   chain that gets the row out of "completed" (where Synthea
     *   parks everything) into the dashboard's default visible bucket.
     * - **`txDate`** is ``DATE NOT NULL`` with no default — we set it
     *   to ``CURDATE()`` so the row passes the schema constraint.
     *   ``date_added`` / ``date_modified`` go to ``NOW()`` so the
     *   chart sort order respects the commit time.
     * - **`usage_category_title` / `request_intent_title`** are NOT
     *   NULL with no default. We write empty strings to match the
     *   Synthea seed shape; ``PrescriptionService`` self-heals empty
     *   values to ``"Home/Community"`` and ``"Order"`` respectively
     *   in :php:meth:`createResultRecordFromDatabaseResult`, so the
     *   downstream FHIR resource gets sensible category / intent.
     * - **`provider_id` / `encounter` / `rxnorm_drugcode`** all NULL.
     *   We don't extract structured RxNorm codes from intake-form
     *   text, the row isn't tied to an encounter, and there's no
     *   ordering provider on file for AI-extracted intake meds. The
     *   FHIR projection tolerates each of these being NULL — they
     *   just don't appear on the resulting MedicationRequest.
     * - **`user`** carries the JWT's looked-up username so the audit
     *   row reflects who clicked Commit (same convention as
     *   :php:meth:`insertAllergyRow`).
     */
    private function insertPrescriptionRow(
        int $patientId,
        string $username,
        PromotionItem $item,
        string $comments,
    ): void {
        [$dose, $dosageInstructions] = $this->parseMedicationDetails($item->details);

        // ``binary(16)`` UUID. PHP 8.2+ guarantees random_bytes is
        // CSPRNG-backed; collision risk is negligible at 122 bits of
        // entropy and the row is unique-keyed on uuid so a collision
        // would surface as a constraint violation, not silent
        // corruption.
        $uuid = random_bytes(16);

        // varchar(150) on ``drug``; clip defensively so a pathological
        // intake-form drug name can't violate the column constraint.
        $drug = substr($item->title, 0, 150);

        $this->connection->executeStatement(
            'INSERT INTO prescriptions ('
            . 'uuid, patient_id, drug, drug_id, dosage, '
            . 'drug_dosage_instructions, active, txDate, '
            . 'date_added, date_modified, '
            . 'usage_category, usage_category_title, '
            . 'request_intent, request_intent_title, '
            . 'note, user, erx_source, erx_uploaded'
            . ') VALUES ('
            . ':uuid, :pid, :drug, 0, :dosage, '
            . ':sig, 1, CURDATE(), '
            . 'NOW(), NOW(), '
            . ":category, '', "
            . ":intent, '', "
            . ':note, :user, 0, 0'
            . ')',
            [
                'uuid' => $uuid,
                'pid' => $patientId,
                'drug' => $drug,
                'dosage' => $dose,
                'sig' => $dosageInstructions,
                'category' => 'community',
                'intent' => 'order',
                'note' => $comments,
                'user' => $username,
            ],
        );
    }

    /**
     * Pulls a (dose, full-sig) tuple out of the panel's ``details``
     * string for the medication path.
     *
     * The ExtractionPanel composes medication details as either
     * ``"<dose>"`` (dose only) or ``"<dose> <frequency>"`` /
     * ``"<dose>, <frequency>"`` / ``"<dose> · <frequency>"``. We try a
     * lightweight split: leading numeric+unit token is the dose, the
     * rest is preserved verbatim as the full sig. Both pieces are
     * persisted (dose → ``prescriptions.dosage`` for legacy UIs,
     * full sig → ``prescriptions.drug_dosage_instructions`` for the
     * FHIR text projection).
     *
     * Returns ``[null, null]`` when ``details`` is empty so the
     * INSERT writes NULL for both columns rather than empty strings.
     *
     * @return array{0: ?string, 1: ?string}
     */
    private function parseMedicationDetails(?string $details): array
    {
        if ($details === null) {
            return [null, null];
        }
        $trimmed = trim($details);
        if ($trimmed === '') {
            return [null, null];
        }

        // Try to peel off a leading "<number><optional space><unit>"
        // token. We accept a unit pattern that matches the common
        // intake-form vocabulary (mg, mcg, g, ml, mEq, IU, %) without
        // being so loose that "metformin" would parse as a unit.
        $matched = preg_match(
            '/^(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|mEq|IU|%)\b/i',
            $trimmed,
            $matches,
        );
        $dose = $matched === 1 ? "{$matches[1]} {$matches[2]}" : null;

        // The full sig string is always preserved as the FHIR text
        // instruction; capping at 1000 chars is a guardrail against a
        // pathological details string blowing up the longtext column.
        $sig = strlen($trimmed) > 1000 ? substr($trimmed, 0, 1000) : $trimmed;

        return [$dose, $sig];
    }

    /**
     * Pulls a round-trip-safe severity bucket out of the panel's
     * ``details`` string.
     *
     * The ExtractionPanel formats allergy details as
     * ``"<reaction-text> (<severity>)"`` (see the panel's docblock at
     * ``vue-ui/src/components/agentforge/ExtractionPanel.vue:160``).
     * The trailing parenthesised group is the structured severity
     * label the LLM extracted; anything outside that pattern is
     * free-form reaction text. We extract the parenthesised value,
     * lowercase + trim it, and only return one of the two
     * round-trip-clean bucket values.
     *
     * Returning ``null`` here means "leave ``severity_al`` unset",
     * which is the correct behaviour for both "no severity in the
     * details string" AND "severity present but doesn't map cleanly"
     * — the frontend's downstream default of "moderate" is the
     * honest answer in both cases.
     */
    private function normaliseSeverityFromDetails(?string $details): ?string
    {
        if ($details === null || $details === '') {
            return null;
        }
        if (preg_match('/\(([^)]+)\)\s*$/', $details, $matches) !== 1) {
            return null;
        }
        $candidate = strtolower(trim($matches[1]));
        if ($candidate === '') {
            return null;
        }
        // Light synonym normalisation — the LLM extraction is
        // free-form, so accept a few common variants users might
        // write into intake forms.
        $candidate = match (true) {
            $candidate === 'low' => 'mild',
            $candidate === 'high', str_contains($candidate, 'life') => 'severe',
            default => $candidate,
        };
        return in_array($candidate, self::ROUND_TRIP_SEVERITIES, true)
            ? $candidate
            : null;
    }

    private function buildComments(
        PromotionItem $item,
        ?string $questionnaireResponseId,
        ?int $documentId,
    ): string {
        $parts = ['Imported from AgentForge intake form'];
        if ($item->details !== null && $item->details !== '') {
            $parts[] = $item->details;
        }
        $lineage = [];
        if ($questionnaireResponseId !== null && $questionnaireResponseId !== '') {
            $lineage[] = "qr_id={$questionnaireResponseId}";
        }
        if ($documentId !== null) {
            $lineage[] = "doc_id={$documentId}";
        }
        if (count($lineage) > 0) {
            $parts[] = '(' . implode(', ', $lineage) . ')';
        }
        return implode(' ', $parts);
    }
}
