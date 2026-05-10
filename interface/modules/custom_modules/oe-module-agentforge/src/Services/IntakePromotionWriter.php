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
 * Writes one row to the legacy ``lists`` table per accepted item type:
 *
 *  - allergies      → ``type='allergy'``
 *  - problems       → ``type='medical_problem'``
 *  - medications    → ``type='medication'``
 *  - family history → ``type='family_history'``
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
 *   already drive ``AllergiesCard`` / ``ProblemListCard`` /
 *   ``MedicationsCard``) read back — so the cards will refresh and
 *   render the new rows as soon as the cache invalidates.
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
            $listsId = $this->insertLists(
                $patientId,
                $username,
                $questionnaireResponseId,
                $documentId,
                $item,
            );
            $handles[] = new PromotedItemHandle(
                kind: $item->kind,
                listsId: $listsId,
                title: $item->title,
            );
        }
        return new PromotionResult($handles);
    }

    private function insertLists(
        int $patientId,
        string $username,
        ?string $questionnaireResponseId,
        ?int $documentId,
        PromotionItem $item,
    ): int {
        $comments = $this->buildComments($item, $questionnaireResponseId, $documentId);

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
