<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\AgentForge;

use Doctrine\DBAL\Connection;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Modules\AgentForge\Services\BreakglassAuditWriter;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for BreakglassAuditWriter — the only place AgentForge calls
 * EventAuditLogger->newEvent() for breakglass-flagged turns.
 *
 * The PHI-bearing reason text MUST land in the `comments` argument of
 * newEvent() so EventAuditLogger's encryption-or-base64 path covers it.
 * If the reason ever shifts to another argument, the comments column
 * for the breakglass row goes empty and the audit trail is broken.
 */
final class BreakglassAuditWriterTest extends TestCase
{
    #[Test]
    public function recordCallsEventAuditLoggerWithBreakglassMarkerAndPid(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::equalTo('agentforge-breakglass'),
                self::equalTo('dr.smith'),
                self::equalTo(''),
                self::equalTo(1),
                self::stringContains('Emergency department visit'),
                self::equalTo(7),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'Emergency department visit');
    }

    #[Test]
    public function recordLooksUpUsernameFromUsersTableByUserId(): void
    {
        $connection = self::createMock(Connection::class);
        $connection
            ->expects(self::once())
            ->method('fetchOne')
            ->with(
                self::stringContains('FROM users'),
                self::equalTo([42]),
            )
            ->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger->expects(self::once())->method('newEvent');

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }

    #[Test]
    public function recordPassesLookedUpUsernameAsUserArgument(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::equalTo('dr.smith'),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }

    #[Test]
    public function recordFallsBackToUserIdSuffixWhenUsersRowMissing(): void
    {
        // The audit write must not fail because the users table is somehow
        // out of sync — the audit trail is the priority, username
        // readability is secondary. Falls back to "user-{id}".
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn(false);

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::equalTo('user-42'),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }

    #[Test]
    public function recordFallsBackWhenUsernameIsEmptyString(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::equalTo('user-42'),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }

    #[Test]
    public function recordEmbedsReasonTextInCommentsArgument(): void
    {
        // The `comments` column is the PHI-bearing field that
        // EventAuditLogger encrypts (or base64s when encryption is off).
        // The reason text MUST land there so it is covered by the
        // existing encryption pipeline.
        $reason = 'After-hours consult — primary care unavailable.';

        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::stringContains($reason),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, $reason);
    }

    #[Test]
    public function recordMarksAuditEventAsSuccess(): void
    {
        // success=1 — the breakglass access itself succeeded; the reason
        // is what makes the audit row interesting, not a failure flag.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::equalTo(1),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }

    #[Test]
    public function recordPassesPatientIdAsAuditPatientArgument(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::equalTo(7),
            );

        $writer = new BreakglassAuditWriter($connection, $auditLogger);
        $writer->record(42, 7, 'reason text');
    }
}
