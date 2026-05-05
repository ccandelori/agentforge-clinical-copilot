<?php

/**
 * LabPersistAuditWriter — fires the `agentforge.lab_persist` event-log
 * entry on the AgentForge lab-PDF persistence flow's success path.
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
 * Mirrors :class:`IntakePersistAuditWriter` for the lab path. The event
 * carries the procedure_result IDs (in the comments field) so audit
 * dashboards can pivot from the event row back to the actual results.
 *
 * Same discipline as the intake audit: only fires on the successful
 * write path. Failure paths short-circuit upstream so the audit log
 * stays 1:1 with rows actually inserted into procedure_result.
 */
class LabPersistAuditWriter
{
    public const EVENT_NAME = 'agentforge.lab_persist';

    public function __construct(
        private readonly Connection $connection,
        private readonly EventAuditLogger $auditLogger,
    ) {
    }

    /**
     * @param list<int> $procedureResultIds
     */
    public function record(
        int $userId,
        int $patientId,
        array $procedureResultIds,
        string $extractionStatus,
    ): void {
        $username = $this->lookupUsername($userId);
        $idList = implode(',', $procedureResultIds);
        $comments = sprintf(
            'AgentForge lab persist: result_ids=[%s], status=%s',
            $idList,
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

        return "user-{$userId}";
    }
}
