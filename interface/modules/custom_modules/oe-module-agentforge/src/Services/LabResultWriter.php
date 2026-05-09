<?php

/**
 * LabResultWriter — multi-table cascade INSERT into the
 * procedure_order / procedure_report / procedure_result triplet for the
 * AgentForge lab-PDF persistence flow.
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
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\AgentForge\Domain\LabValue;

/**
 * Writes one procedure_order, one procedure_report, and N
 * procedure_result rows (one per LabValue) atomically inside a DBAL
 * transaction. If any insert fails the whole cascade rolls back —
 * partial writes would leave dangling reports without orders or
 * results without reports, both of which break the EHR's lab list UI.
 *
 * Mapping decisions worth knowing:
 *
 * - **No encounter linkage.** `encounter_id` defaults to 0 because the
 *   sidecar lab-PDF flow doesn't go through an encounter — it's a
 *   direct patient-document upload, no visit context. Clinicians can
 *   re-attach the result to an encounter via the OpenEMR UI later.
 * - **No ordering provider.** `provider_id` defaults to 0 for the same
 *   reason. The actual ordering provider is recoverable from the PDF
 *   text via `LabPdfExtraction.ordering_provider`, but mapping a
 *   freeform string to a `users.id` is out of scope here; the value
 *   lives in the citation's source text and the optional
 *   `ordering_provider` field for downstream reconciliation.
 * - **`order_status` = 'complete', `review_status` = 'received'.** The
 *   results came in but a clinician hasn't reviewed them yet. The lab
 *   list UI surfaces 'received' rows in its inbox; that's exactly the
 *   shape we want for AI-extracted results.
 * - **`abnormal` is normalized to the 4-value scheme** the column
 *   accepts (`no/yes/high/low`). The CRITICAL_HIGH and CRITICAL_LOW
 *   variants from {@see AbnormalFlag} preserve the criticality marker
 *   in the result's `comments` field so it isn't lost — the column's
 *   schema doesn't carry it natively, but the synthesizer can recover
 *   it from comments when surfacing flagged values.
 * - **`procedure_result.document_id`** is set to the source PDF's
 *   document_id so the overlay UI can navigate back to the bytes.
 *
 * The class does NOT touch `procedure_order_code` (mapped tests),
 * `procedure_specimen`, or `procedure_questions`. Those are out of
 * scope — the AI-extracted result is the unapproved record; clinician
 * review through the existing OpenEMR UI is what populates those
 * downstream tables.
 */
class LabResultWriter
{
    public const ORDER_STATUS_COMPLETE = 'complete';
    public const REPORT_STATUS_COMPLETE = 'complete';
    public const REVIEW_STATUS_RECEIVED = 'received';
    public const RESULT_STATUS_FINAL = 'final';
    public const RESULT_DATA_TYPE_STRING = 'S';

    public function __construct(
        private readonly Connection $connection,
    ) {
    }

    /**
     * @param list<LabValue> $values Already-parsed lab rows. The
     *     constructor on {@see LabValue} enforces that test_name and
     *     value are non-empty strings, so the writer can trust them
     *     without re-validating — controller-level parsing is the
     *     single source of truth for "is this row safe to persist?".
     */
    public function persist(
        int $patientId,
        int $userId,
        int $documentId,
        ?string $orderingProvider,
        ?string $accessionNumber,
        array $values,
    ): LabResultIds {
        return $this->connection->transactional(
            fn () => $this->insertCascade(
                $patientId,
                $userId,
                $documentId,
                $orderingProvider,
                $accessionNumber,
                $values,
            ),
        );
    }

    /**
     * @param list<LabValue> $values
     */
    private function insertCascade(
        int $patientId,
        int $userId,
        int $documentId,
        ?string $orderingProvider,
        ?string $accessionNumber,
        array $values,
    ): LabResultIds {
        // procedure_order: one per persistence call (this lab PDF is
        // one logical "order").
        $orderUuid = (new UuidRegistry(['table_name' => 'procedure_order']))->createUuid();
        $clinicalHx = $orderingProvider !== null
            ? "Ordering provider (per source PDF): {$orderingProvider}"
            : '';
        $externalId = $accessionNumber !== null && $accessionNumber !== ''
            ? substr($accessionNumber, 0, 20)
            : null;

        $this->connection->executeStatement(
            'INSERT INTO procedure_order '
            . '(uuid, provider_id, patient_id, encounter_id, date_ordered, '
            . 'order_status, activity, clinical_hx, external_id, '
            . 'procedure_order_type) '
            . 'VALUES (:uuid, 0, :pid, 0, CURRENT_TIMESTAMP, '
            . ':order_status, 1, :clinical_hx, :external_id, :order_type)',
            [
                'uuid' => $orderUuid,
                'pid' => $patientId,
                'order_status' => self::ORDER_STATUS_COMPLETE,
                'clinical_hx' => $clinicalHx,
                'external_id' => $externalId,
                'order_type' => 'laboratory_test',
            ],
        );
        $procedureOrderId = (int) $this->connection->lastInsertId();

        // procedure_report: one per call (the lab PDF is one report).
        $reportUuid = (new UuidRegistry(['table_name' => 'procedure_report']))->createUuid();
        $this->connection->executeStatement(
            'INSERT INTO procedure_report '
            . '(uuid, procedure_order_id, procedure_order_seq, date_report, '
            . 'source, report_status, review_status) '
            . 'VALUES (:uuid, :order_id, 1, CURRENT_TIMESTAMP, '
            . ':source, :report_status, :review_status)',
            [
                'uuid' => $reportUuid,
                'order_id' => $procedureOrderId,
                'source' => $userId,
                'report_status' => self::REPORT_STATUS_COMPLETE,
                'review_status' => self::REVIEW_STATUS_RECEIVED,
            ],
        );
        $procedureReportId = (int) $this->connection->lastInsertId();

        // procedure_result: one per LabValue.
        $resultIds = [];
        foreach ($values as $value) {
            $resultUuid = (new UuidRegistry(['table_name' => 'procedure_result']))->createUuid();
            [$abnormalCode, $criticalityNote] = self::mapAbnormalFlag(
                $value->abnormalFlag ?? 'unknown',
            );

            // test_name and value are guaranteed non-empty by the
            // LabValue constructor; optional fields fall back to the
            // empty-string the column historically stores when blank.
            $resultText = substr($value->testName, 0, 255);
            $resultCode = $value->loincCode !== null ? substr($value->loincCode, 0, 31) : '';
            $resultValue = substr($value->value, 0, 255);
            $units = $value->unit !== null ? substr($value->unit, 0, 31) : '';
            $range = $value->referenceRange !== null ? substr($value->referenceRange, 0, 255) : '';
            $collectionDate = $value->collectionDate;

            $this->connection->executeStatement(
                'INSERT INTO procedure_result '
                . '(uuid, procedure_report_id, result_data_type, result_code, '
                . 'result_text, date, units, result, `range`, abnormal, '
                . 'comments, document_id, result_status) '
                . 'VALUES (:uuid, :report_id, :data_type, :code, '
                . ':text, :date, :units, :result, :range, :abnormal, '
                . ':comments, :document_id, :status)',
                [
                    'uuid' => $resultUuid,
                    'report_id' => $procedureReportId,
                    'data_type' => self::RESULT_DATA_TYPE_STRING,
                    'code' => $resultCode,
                    'text' => $resultText,
                    'date' => $collectionDate,
                    'units' => $units,
                    'result' => $resultValue,
                    'range' => $range,
                    'abnormal' => $abnormalCode,
                    'comments' => $criticalityNote,
                    'document_id' => $documentId,
                    'status' => self::RESULT_STATUS_FINAL,
                ],
            );
            $resultIds[] = (int) $this->connection->lastInsertId();
        }

        return new LabResultIds(
            procedureOrderId: $procedureOrderId,
            procedureReportId: $procedureReportId,
            procedureResultIds: $resultIds,
        );
    }

    /**
     * Map AbnormalFlag.value to the 4-value scheme the
     * `procedure_result.abnormal` column accepts (`no/yes/high/low`),
     * preserving CRITICAL_HIGH/CRITICAL_LOW criticality in a comments
     * note since the column itself can't carry it.
     *
     * @return array{0: string, 1: string} [abnormal_code, comments_note]
     */
    private static function mapAbnormalFlag(string $flag): array
    {
        return match ($flag) {
            'normal' => ['no', ''],
            'high' => ['high', ''],
            'low' => ['low', ''],
            'critical_high' => ['high', 'CRITICAL_HIGH (clinical priority — VLM-flagged)'],
            'critical_low' => ['low', 'CRITICAL_LOW (clinical priority — VLM-flagged)'],
            default => ['', ''], // 'unknown' or any unexpected value
        };
    }
}
