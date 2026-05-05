<?php

/**
 * IntakePersistAuditWriter — fires the
 * `agentforge.questionnaire_persist` event-log entry for the intake-form
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
use OpenEMR\Common\Logging\EventAuditLogger;

/**
 * Mirrors :class:`BreakglassAuditWriter` for the intake-persist flow.
 * The audit event records who (the JWT user_id, looked up to a
 * username), for which patient (the JWT's patient_id), with what
 * outcome handle (the new questionnaire_response_id), and at what
 * extraction status (worker's confidence + unsupported_fields shape).
 *
 * Failure paths do NOT call here. The controller wires this only on
 * the successful-write code path so the audit log is always exactly
 * one entry per successful persist (no orphaned events on 401/403/500,
 * no double-events on retries).
 */
readonly class IntakePersistAuditWriter
{
    public const EVENT_NAME = 'agentforge.questionnaire_persist';

    public function __construct(
        private Connection $connection,
        private EventAuditLogger $auditLogger,
    ) {
    }

    public function record(
        int $userId,
        int $patientId,
        string $questionnaireResponseId,
        string $extractionStatus,
    ): void {
        $username = $this->lookupUsername($userId);
        $comments = sprintf(
            'AgentForge intake persist: response_id=%s, status=%s',
            $questionnaireResponseId,
            $extractionStatus,
        );

        $this->auditLogger->newEvent(
            self::EVENT_NAME,
            $username,
            '',
            1,
            $comments,
            $patientId,
        );
    }

    private function lookupUsername(int $userId): string
    {
        $row = $this->connection->fetchOne(
            'SELECT username FROM users WHERE id = ?',
            [$userId],
        );

        if (is_string($row) && $row !== '') {
            return $row;
        }

        // Mirror BreakglassAuditWriter's fallback: never let a missing
        // users row block the audit write itself.
        return "user-{$userId}";
    }
}
