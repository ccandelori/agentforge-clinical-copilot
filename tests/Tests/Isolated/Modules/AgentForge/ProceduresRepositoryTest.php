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

use DateTimeImmutable;
use Doctrine\DBAL\Connection;
use Lcobucci\Clock\FrozenClock;
use OpenEMR\Modules\AgentForge\Services\ProceduresRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;

/**
 * Behavior tests for ProceduresRepository.
 *
 * The repository is the SQL contract between OpenEMR's procedure_order
 * table and the agent's get_procedures tool. Procedures share storage
 * with labs (same procedure_order rows) and the clinical separator is
 * "has procedure_result rows" — labs do, true procedures (referrals,
 * screenings, surgeries) don't. Tests here lock that discriminator,
 * the dedup-by-code (annual screenings collapse to one row), and the
 * date-only wire format the agent consumes.
 */
final class ProceduresRepositoryTest extends TestCase
{
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function findRecentByPidQueriesProcedureOrderJoinedWithCode(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    self::assertStringContainsString('procedure_order po', $sql);
                    // procedure_name lives on procedure_order_code, not
                    // procedure_order — joining is mandatory for the
                    // human-readable name to flow through.
                    self::assertStringContainsString('procedure_order_code', $sql);
                    self::assertStringContainsString('po.patient_id = :pid', $sql);
                    return true;
                }),
                self::anything(),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function findRecentByPidExcludesOrdersWithProcedureResults(): void
    {
        // The clinical separator between procedures and labs: orders that
        // have procedure_result rows ARE labs (BMP/CBC/A1c with a numeric
        // value); orders without results are true procedures (referrals,
        // PHQ-9 screenings, surgical interventions). The SQL must filter
        // out result-bearing rows so the agent's procedures view doesn't
        // duplicate the labs view.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    self::assertStringContainsString('NOT EXISTS', $sql);
                    self::assertStringContainsString('procedure_result', $sql);
                    return true;
                }),
                self::anything(),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function findRecentByPidFiltersToCompletedOrdersOnly(): void
    {
        // Pending / canceled / in-progress orders aren't part of the
        // patient's procedure history yet. Only completed counts.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    self::assertStringContainsString("order_status = 'completed'", $sql);
                    return true;
                }),
                self::anything(),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function findRecentByPidDeduplicatesByProcedureCode(): void
    {
        // Synthea generates an annual depression screening, AUDIT, and so
        // on — for a multi-year patient that's 6+ rows of the same
        // procedure code. The agent reasons better over "depression
        // screening completed (most recent: 2026-03-06)" than over six
        // identical rows. Same dedup pattern as ProblemsRepository.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    self::assertStringContainsString('ROW_NUMBER()', $sql);
                    self::assertStringContainsString('PARTITION BY', $sql);
                    self::assertStringContainsString('procedure_code', $sql);
                    self::assertStringContainsString('WHERE rn = 1', $sql);
                    return true;
                }),
                self::anything(),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function findRecentByPidProjectsDateOnlyDateOrdered(): void
    {
        // Wire-format contract: dates are date-only YYYY-MM-DD strings,
        // not DATETIME. Same constraint as labs/immunizations/problems —
        // the sidecar's pydantic ``date | None`` field rejects datetime
        // strings with non-zero time.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    self::assertStringContainsString('DATE(po.date_ordered)', $sql);
                    return true;
                }),
                self::anything(),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function findRecentByPidUsesDefaultSinceDaysOf365(): void
    {
        // Procedures are typically annual or rarer; a 365-day default
        // window catches all the recurring screenings without
        // overwhelming the LLM with a decade of dental cleanings.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::anything(),
                self::callback(function (array $params): bool {
                    self::assertSame(42, $params['pid']);
                    // 365 days before 2026-04-30 = 2025-04-30
                    self::assertSame('2025-04-30 00:00:00', $params['since']);
                    return true;
                }),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function customSinceDaysPropagatesIntoCutoff(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::anything(),
                self::callback(function (array $params): bool {
                    self::assertSame('2026-03-31 00:00:00', $params['since']);
                    return true;
                }),
            )
            ->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42, 30);
    }

    #[Test]
    public function findRecentByPidReturnsEmptyArrayWhenNoRowsMatch(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        self::assertSame([], $repo->findRecentByPid(42));
    }

    #[Test]
    public function findRecentByPidMapsRowsToTypedShape(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 4321,
                'procedure_code' => 'SNOMED CT:171207006',
                'procedure_name' => 'Depression screening (procedure)',
                'date_ordered' => '2026-03-06',
                'status' => 'completed',
                'encounter_id' => 78,
            ],
        ]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertCount(1, $rows);
        self::assertSame(4321, $rows[0]['id']);
        self::assertSame('SNOMED CT:171207006', $rows[0]['procedure_code']);
        self::assertSame('Depression screening (procedure)', $rows[0]['procedure_name']);
        self::assertSame('2026-03-06', $rows[0]['date_ordered']);
        self::assertSame('completed', $rows[0]['status']);
        self::assertSame(78, $rows[0]['encounter_id']);
    }

    #[Test]
    public function findRecentByPidCoercesEmptyStringsToNull(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 1,
                'procedure_code' => '',
                'procedure_name' => '',
                'date_ordered' => null,
                'status' => '',
                'encounter_id' => 0,
            ],
        ]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertNull($rows[0]['procedure_code']);
        self::assertNull($rows[0]['procedure_name']);
        self::assertNull($rows[0]['date_ordered']);
        self::assertNull($rows[0]['status']);
        // encounter_id is int|null — 0 is the legacy "no encounter" sentinel
        // and we coerce it to null at the JSON boundary.
        self::assertNull($rows[0]['encounter_id']);
    }

    #[Test]
    public function findRecentByPidCoercesStringIdsToInt(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => '99',
                'procedure_code' => 'SNOMED CT:73761001',
                'procedure_name' => 'Colonoscopy (procedure)',
                'date_ordered' => '2024-08-15',
                'status' => 'completed',
                'encounter_id' => '12',
            ],
        ]);

        $repo = new ProceduresRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertSame(99, $rows[0]['id']);
        self::assertSame(12, $rows[0]['encounter_id']);
    }

    private function makeClock(): ClockInterface
    {
        return new FrozenClock(new DateTimeImmutable(self::TEST_NOW));
    }
}
