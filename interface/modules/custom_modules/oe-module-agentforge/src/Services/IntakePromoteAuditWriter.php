<?php

/**
 * IntakePromoteAuditWriter — fires the
 * `agentforge.intake_promote` event-log entry for the clinician-
 * approved chart-write that follows an intake-form extraction.
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
 * Records who promoted which AI-extracted intake rows into the
 * structured chart, for which patient, and at what scale (count of
 * inserted rows). Mirrors :class:`IntakePersistAuditWriter` for the
 * companion "extract → store as QR" event so a reviewer chasing a
 * single chart row can find the upstream extraction's audit trail by
 * filtering on the same patient_id and the agentforge.* events.
 *
 * Only fires on the successful 201 path of the controller — failed
 * promotions don't audit (no row was inserted) and partial failures
 * are reported as 500 with no audit entry (the writer's
 * transactional() rolls back on any per-row failure, so partial
 * promotion is not a state we can land in).
 */
readonly class IntakePromoteAuditWriter
{
    public const EVENT_NAME = 'agentforge.intake_promote';

    public function __construct(
        private Connection $connection,
        private EventAuditLogger $auditLogger,
    ) {
    }

    public function record(
        int $userId,
        int $patientId,
        ?string $questionnaireResponseId,
        int $promotedCount,
    ): void {
        $username = $this->lookupUsername($userId);
        $qrSegment = $questionnaireResponseId !== null && $questionnaireResponseId !== ''
            ? ", qr_id={$questionnaireResponseId}"
            : '';
        $comments = sprintf(
            'AgentForge intake promote: %d row(s) committed to chart%s',
            $promotedCount,
            $qrSegment,
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
