<?php

/**
 * BreakglassAuditWriter — wraps EventAuditLogger for the AgentForge
 * sidecar's breakglass audit-log endpoint.
 *
 * The Python sidecar calls the breakglass endpoint each time a
 * breakglass-flagged user opens a chart through the agent. This writer
 * is the single place where AgentForge calls EventAuditLogger->newEvent()
 * for that flow — the reason text supplied by the user lands in the
 * `comments` argument so EventAuditLogger's encryption (or base64 fallback,
 * controlled by `enable_auditlog_encryption`) covers it.
 *
 * The audit row carries the human username (looked up from the users
 * table by user_id) so the audit-log UI shows readable strings rather
 * than bare integers. If the users row is missing (which would normally
 * indicate a deeper inconsistency) the writer falls back to "user-{id}"
 * so the audit write itself never fails — the audit is the priority,
 * username readability is secondary.
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

final readonly class BreakglassAuditWriter
{
    /**
     * Stable event-name marker for AgentForge breakglass rows. Picking a
     * single string and sticking with it lets future audit dashboards
     * filter the breakglass-via-agent flow apart from the legacy
     * breakglass-via-UI flow.
     */
    public const EVENT_NAME = 'agentforge-breakglass';

    public function __construct(
        private Connection $connection,
        private EventAuditLogger $auditLogger,
    ) {
    }

    /**
     * Record a breakglass audit event. The reason text is embedded in the
     * `comments` field of EventAuditLogger->newEvent(), which is the
     * PHI-bearing field covered by the audit-log encryption pipeline.
     */
    public function record(int $userId, int $patientId, string $reason): void
    {
        $username = $this->lookupUsername($userId);
        $comments = "AgentForge breakglass access: {$reason}";

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

        // Fallback so the audit write never fails on a missing users
        // row — see the class docblock for rationale.
        return "user-{$userId}";
    }
}
