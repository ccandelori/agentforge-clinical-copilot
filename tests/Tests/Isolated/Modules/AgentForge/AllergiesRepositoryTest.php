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
use OpenEMR\Modules\AgentForge\Services\AllergiesRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for AllergiesRepository.
 *
 * Mirrors the medications/problems repository contract: read active rows
 * from the lists table for a given pid, mapping the legacy schema's
 * empty-string defaults to nulls so downstream JSON consumers see the
 * absence of data as null rather than "".
 */
final class AllergiesRepositoryTest extends TestCase
{
    #[Test]
    public function findActiveByPidQueriesListsTableWithAllergyDiscriminator(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'FROM lists')
                        && str_contains($sql, "type = 'allergy'")
                        && str_contains($sql, 'activity = 1')
                        && str_contains($sql, 'pid = :pid');
                }),
                ['pid' => 123]
            )
            ->willReturn([]);

        $repository = new AllergiesRepository($connection);

        $repository->findActiveByPid(123);
    }

    #[Test]
    public function findActiveByPidReturnsEmptyArrayWhenNoRowsMatch(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([]);

        $repository = new AllergiesRepository($connection);

        self::assertSame([], $repository->findActiveByPid(123));
    }

    #[Test]
    public function findActiveByPidMapsRowsToTypedShape(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 7,
                'title' => 'Penicillin',
                'reaction' => 'Anaphylaxis',
                'severity_al' => 'severe',
                'begdate' => '2018-03-01',
                'enddate' => null,
            ],
        ]);

        $repository = new AllergiesRepository($connection);
        $rows = $repository->findActiveByPid(123);

        self::assertCount(1, $rows);
        self::assertSame(7, $rows[0]['id']);
        self::assertSame('Penicillin', $rows[0]['name']);
        self::assertSame('Anaphylaxis', $rows[0]['reaction']);
        self::assertSame('severe', $rows[0]['severity']);
        self::assertSame('2018-03-01', $rows[0]['begin_date']);
        self::assertNull($rows[0]['end_date']);
    }

    #[Test]
    public function findActiveByPidCoercesEmptyReactionStringToNull(): void
    {
        // lists.reaction is `VARCHAR(255) NOT NULL DEFAULT ''` in the schema,
        // so a missing reaction comes through as the empty string. The JSON
        // contract with the sidecar treats absence as null.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 1,
                'title' => 'Latex',
                'reaction' => '',
                'severity_al' => null,
                'begdate' => null,
                'enddate' => null,
            ],
        ]);

        $repository = new AllergiesRepository($connection);
        $rows = $repository->findActiveByPid(1);

        self::assertNull($rows[0]['reaction']);
        self::assertNull($rows[0]['severity']);
        self::assertNull($rows[0]['begin_date']);
        self::assertNull($rows[0]['end_date']);
    }

    #[Test]
    public function findActiveByPidCoercesStringIdToInt(): void
    {
        // PDO drivers can return integer columns as strings depending on
        // driver configuration; the repository normalises that.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => '42',
                'title' => 'Sulfa drugs',
                'reaction' => 'Hives',
                'severity_al' => 'mild',
                'begdate' => null,
                'enddate' => null,
            ],
        ]);

        $repository = new AllergiesRepository($connection);
        $rows = $repository->findActiveByPid(1);

        self::assertSame(42, $rows[0]['id']);
    }

    #[Test]
    public function findActiveByPidPreservesRowOrderFromQuery(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 9,
                'title' => 'Bee stings',
                'reaction' => 'Swelling',
                'severity_al' => 'moderate',
                'begdate' => '2024-06-01',
                'enddate' => null,
            ],
            [
                'id' => 3,
                'title' => 'Penicillin',
                'reaction' => 'Rash',
                'severity_al' => 'mild',
                'begdate' => '2010-01-01',
                'enddate' => null,
            ],
        ]);

        $repository = new AllergiesRepository($connection);
        $rows = $repository->findActiveByPid(1);

        self::assertSame('Bee stings', $rows[0]['name']);
        self::assertSame('Penicillin', $rows[1]['name']);
    }
}
