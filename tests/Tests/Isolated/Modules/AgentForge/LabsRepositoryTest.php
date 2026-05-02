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
use OpenEMR\Modules\AgentForge\Services\LabsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;

/**
 * Behavior tests for LabsRepository.
 *
 * No database: the Doctrine Connection is mocked so we can assert the
 * exact SQL/params shape the repository emits, plus the mapping from
 * raw row data into the typed return shape.
 */
final class LabsRepositoryTest extends TestCase
{
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function findRecentByPidIssuesExpectedSqlWithSinceCutoff(): void
    {
        $connection = $this->createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    // Assert the join chain + WHERE + ORDER BY + LIMIT
                    self::assertStringContainsString('procedure_order po', $sql);
                    self::assertStringContainsString('procedure_report pr', $sql);
                    self::assertStringContainsString('procedure_result pres', $sql);
                    self::assertStringContainsString('po.patient_id = :pid', $sql);
                    self::assertStringContainsString('po.date_ordered >= :since', $sql);
                    // Wire-format contract: the SELECT projects
                    // DATE(po.date_ordered) so the JSON ships date-only
                    // strings. The sidecar's pydantic ``date | None``
                    // field rejects datetime strings with non-zero time.
                    self::assertStringContainsString('DATE(po.date_ordered)', $sql);
                    self::assertStringContainsString('ORDER BY po.date_ordered DESC', $sql);
                    self::assertStringContainsString('pr.procedure_report_id DESC', $sql);
                    self::assertStringContainsString('pres.procedure_result_id ASC', $sql);
                    self::assertStringContainsString('LIMIT 200', $sql);
                    return true;
                }),
                self::callback(function (array $params): bool {
                    self::assertSame(42, $params['pid']);
                    // 90 days before 2026-04-30 = 2026-01-30
                    self::assertSame('2026-01-30 00:00:00', $params['since']);
                    return true;
                }),
            )
            ->willReturn([]);

        $repo = new LabsRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42);
    }

    #[Test]
    public function customSinceDaysPropagatesIntoCutoff(): void
    {
        $connection = $this->createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::anything(),
                self::callback(function (array $params): bool {
                    // 7 days before 2026-04-30 = 2026-04-23
                    self::assertSame('2026-04-23 00:00:00', $params['since']);
                    return true;
                }),
            )
            ->willReturn([]);

        $repo = new LabsRepository($connection, $this->makeClock());
        $repo->findRecentByPid(42, 7);
    }

    #[Test]
    public function findRecentByPidMapsRawRowsIntoTypedShape(): void
    {
        $connection = $this->createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 101,
                'order_id' => 11,
                'report_id' => 22,
                'test_code' => '2160-0',
                'test_name' => 'Creatinine',
                'value' => '1.1',
                'units' => 'mg/dL',
                'reference_range' => '0.6 - 1.2',
                'abnormal' => 'no',
                'date' => '2026-04-15 08:30:00',
            ],
        ]);

        $repo = new LabsRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertCount(1, $rows);
        self::assertSame(101, $rows[0]['id']);
        self::assertSame(11, $rows[0]['order_id']);
        self::assertSame(22, $rows[0]['report_id']);
        self::assertSame('2160-0', $rows[0]['test_code']);
        self::assertSame('Creatinine', $rows[0]['test_name']);
        self::assertSame('1.1', $rows[0]['value']);
        self::assertSame('mg/dL', $rows[0]['units']);
        self::assertSame('0.6 - 1.2', $rows[0]['reference_range']);
        self::assertSame('no', $rows[0]['abnormal']);
        // datetime trimmed to ISO date for the agent.
        self::assertSame('2026-04-15', $rows[0]['date']);
    }

    #[Test]
    public function emptyStringsBecomeNullsInReturnShape(): void
    {
        $connection = $this->createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => '5',  // string from MySQL bigint
                'order_id' => '1',
                'report_id' => '2',
                'test_code' => '',
                'test_name' => '',
                'value' => '',
                'units' => '',
                'reference_range' => '',
                'abnormal' => '',
                'date' => null,
            ],
        ]);

        $repo = new LabsRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertSame(5, $rows[0]['id'], 'String bigint should coerce to int');
        self::assertNull($rows[0]['test_code']);
        self::assertNull($rows[0]['test_name']);
        self::assertNull($rows[0]['value']);
        self::assertNull($rows[0]['units']);
        self::assertNull($rows[0]['reference_range']);
        self::assertNull($rows[0]['abnormal']);
        self::assertNull($rows[0]['date']);
    }

    #[Test]
    public function emptyResultSetReturnsEmptyArray(): void
    {
        $connection = $this->createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([]);

        $repo = new LabsRepository($connection, $this->makeClock());
        self::assertSame([], $repo->findRecentByPid(42));
    }

    #[Test]
    public function multipleRowsArePreservedInOrderFromTheConnection(): void
    {
        // The repository does not re-sort; ordering is the database's
        // job (we already specify ORDER BY in the SQL). Documents the
        // invariant so a future "tidy up the data here" change can't
        // silently swap the order.
        $connection = $this->createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            ['id' => 3, 'order_id' => 1, 'report_id' => 1, 'date' => null],
            ['id' => 2, 'order_id' => 1, 'report_id' => 1, 'date' => null],
            ['id' => 1, 'order_id' => 1, 'report_id' => 1, 'date' => null],
        ]);

        $repo = new LabsRepository($connection, $this->makeClock());
        $rows = $repo->findRecentByPid(42);

        self::assertSame(3, $rows[0]['id']);
        self::assertSame(2, $rows[1]['id']);
        self::assertSame(1, $rows[2]['id']);
    }

    private function makeClock(): ClockInterface
    {
        return new FrozenClock(new DateTimeImmutable(self::TEST_NOW));
    }
}
