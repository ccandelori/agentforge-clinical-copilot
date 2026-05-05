<?php

/**
 * IntakeQuestionnaireResponseWriter — single-purpose INSERT into the
 * `questionnaire_response` table for the AgentForge intake-form
 * persistence flow.
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

/**
 * Writes the QuestionnaireResponse row produced by the intake-form
 * persistence endpoint. Deliberately narrow surface — the controller
 * has already done JWT validation, the triple-check, and the FHIR
 * mapping before calling here. This class is a single INSERT.
 *
 * What this writer does NOT do:
 *
 * - It does not touch the structured EHR tables (`patient_data`,
 *   `lists`, `medications`, `allergies`, `family_history`). Those
 *   only get populated when a clinician explicitly approves on the
 *   overlay UI; the QuestionnaireResponse is the unapproved record.
 * - It does not fire the audit event — that's a separate concern
 *   the controller orchestrates so a write failure doesn't double-
 *   audit and a successful write always pairs with exactly one event.
 *
 * The returned response_id is the string-form UUID, suitable for
 * surfacing back to the caller as the new resource handle.
 */
readonly class IntakeQuestionnaireResponseWriter
{
    public const STATUS_COMPLETED = 'completed';

    public function __construct(
        private Connection $connection,
    ) {
    }

    /**
     * @param array<string, mixed> $questionnaireResponse FHIR R4 JSON
     * @return string Newly assigned `response_id` (string-form UUID).
     */
    public function insert(
        int $patientId,
        int $questionnaireForeignId,
        string $questionnaireName,
        array $questionnaireResponse,
        string $questionnaireJson,
    ): string {
        $qrUuidBinary = (new UuidRegistry(['table_name' => 'questionnaire_response']))->createUuid();
        $responseId = UuidRegistry::uuidToString($qrUuidBinary);

        $this->connection->executeStatement(
            'INSERT INTO questionnaire_response '
            . '(uuid, response_id, questionnaire_foreign_id, questionnaire_name, '
            . 'patient_id, create_time, last_updated, version, status, '
            . 'questionnaire, questionnaire_response) '
            . 'VALUES (:uuid, :response_id, :q_fk, :q_name, :pid, '
            . 'CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, :status, :q_json, :qr_json)',
            [
                'uuid' => $qrUuidBinary,
                'response_id' => $responseId,
                'q_fk' => $questionnaireForeignId,
                'q_name' => $questionnaireName,
                'pid' => $patientId,
                'status' => self::STATUS_COMPLETED,
                'q_json' => $questionnaireJson,
                'qr_json' => json_encode(
                    $questionnaireResponse,
                    JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES,
                ),
            ],
        );

        return $responseId;
    }
}
