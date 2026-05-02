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
use OpenEMR\Modules\AgentForge\Services\ProblemsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for ProblemsRepository.
 *
 * The repository is the SQL contract between OpenEMR's lists table and
 * the agent's get_active_problems tool. Tests here lock the query shape
 * (filters, dedup, date cast) so a future refactor can't silently
 * regress the clinically-relevant subset the agent receives.
 */
final class ProblemsRepositoryTest extends TestCase
{
    #[Test]
    public function findActiveByPidQueriesListsTableWithMedicalProblemDiscriminator(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'FROM lists')
                        && str_contains($sql, "type = 'medical_problem'")
                        && str_contains($sql, 'activity = 1')
                        && str_contains($sql, 'pid = :pid');
                }),
                ['pid' => 123]
            )
            ->willReturn([]);

        $repository = new ProblemsRepository($connection);
        $repository->findActiveByPid(123);
    }

    #[Test]
    public function findActiveByPidExcludesSituationConceptClass(): void
    {
        // SNOMED "(situation)" rows are administrative codes
        // (e.g. "Medication review due (situation)") that don't belong
        // on a clinician-facing problem list. The SQL must filter them
        // out before the data reaches the agent.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, "title NOT LIKE '%(situation)%'");
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ProblemsRepository($connection);
        $repository->findActiveByPid(123);
    }

    #[Test]
    public function findActiveByPidDeduplicatesByDiagnosisCode(): void
    {
        // Synthea creates one lists row per encounter that touched a
        // condition, so a single chronic problem can repeat 6+ times.
        // The SQL must collapse to one row per distinct SNOMED code,
        // keeping the most-recent occurrence.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'ROW_NUMBER()')
                        && str_contains($sql, 'PARTITION BY diagnosis')
                        && str_contains($sql, 'WHERE rn = 1');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ProblemsRepository($connection);
        $repository->findActiveByPid(123);
    }

    #[Test]
    public function findActiveByPidProjectsDateOnlyBegdate(): void
    {
        // Wire-format contract: dates are date-only YYYY-MM-DD strings,
        // not DATETIME. Without the DATE() cast Synthea-imported rows
        // ship "2026-02-06 17:32:52" and the sidecar's pydantic
        // ``date | None`` field rejects them.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'DATE(begdate)');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ProblemsRepository($connection);
        $repository->findActiveByPid(123);
    }

    #[Test]
    public function findActiveByPidReturnsEmptyArrayWhenNoRowsMatch(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([]);

        $repository = new ProblemsRepository($connection);
        self::assertSame([], $repository->findActiveByPid(123));
    }

    #[Test]
    public function findActiveByPidMapsRowsToTypedShape(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 42,
                'title' => 'Essential hypertension (disorder)',
                'diagnosis' => 'SNOMED-CT:59621000',
                'begdate' => '2010-09-01',
            ],
            [
                'id' => 43,
                'title' => 'Anemia',
                'diagnosis' => null,
                'begdate' => null,
            ],
        ]);

        $repository = new ProblemsRepository($connection);
        $rows = $repository->findActiveByPid(123);

        self::assertCount(2, $rows);
        self::assertSame(42, $rows[0]['id']);
        self::assertSame('Essential hypertension (disorder)', $rows[0]['title']);
        self::assertSame('SNOMED-CT:59621000', $rows[0]['diagnosis']);
        self::assertSame('2010-09-01', $rows[0]['begin_date']);
        self::assertNull($rows[1]['diagnosis']);
        self::assertNull($rows[1]['begin_date']);
    }
}
