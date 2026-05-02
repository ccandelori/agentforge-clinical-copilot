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
use OpenEMR\Modules\AgentForge\Services\ImmunizationsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for ImmunizationsRepository.
 *
 * The repository is the SQL contract between OpenEMR's immunizations
 * table and the agent's get_immunizations tool. Tests here lock the
 * query shape (filters, codes-table CVX -> vaccine_name lookup,
 * date cast, erroneous-row exclusion) so a future refactor can't
 * silently regress what the agent receives.
 */
final class ImmunizationsRepositoryTest extends TestCase
{
    #[Test]
    public function findByPidQueriesImmunizationsTableScopedToPatient(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'FROM immunizations')
                        && str_contains($sql, 'patient_id = :pid');
                }),
                ['pid' => 123]
            )
            ->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        $repository->findByPid(123);
    }

    #[Test]
    public function findByPidExcludesErroneouslyAddedRows(): void
    {
        // immunizations.added_erroneously is the soft-delete flag; rows
        // marked 1 are mistakes the user retracted. They must not reach
        // the agent.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'added_erroneously');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        $repository->findByPid(123);
    }

    #[Test]
    public function findByPidJoinsCodesTableForVaccineNameByCvxLookup(): void
    {
        // CVX codes (140 = "Influenza, seasonal, injectable, preservative
        // free") live in the codes table at code_type=100. Without this
        // lookup the agent would only see numeric codes and the response
        // would read "CVX 140 administered" instead of "annual influenza
        // vaccine administered".
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'codes')
                        && str_contains($sql, 'code_type = 100')
                        // Multiple variants per CVX (different brands)
                        // — pick one canonical text.
                        && str_contains($sql, 'LIMIT 1');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        $repository->findByPid(123);
    }

    #[Test]
    public function findByPidProjectsDateOnlyAdministeredDate(): void
    {
        // Wire-format contract: dates are date-only YYYY-MM-DD strings,
        // not DATETIME. Without the DATE() cast Synthea-imported rows
        // ship "2025-07-11 00:00:00" and the sidecar's pydantic
        // ``date | None`` field rejects them on non-zero time.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'DATE(');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        $repository->findByPid(123);
    }

    #[Test]
    public function findByPidOrdersMostRecentAdministrationFirst(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAllAssociative')
            ->with(
                self::callback(function (string $sql): bool {
                    return str_contains($sql, 'ORDER BY')
                        && str_contains($sql, 'administered_date DESC');
                }),
                self::anything()
            )
            ->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        $repository->findByPid(123);
    }

    #[Test]
    public function findByPidReturnsEmptyArrayWhenNoRowsMatch(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([]);

        $repository = new ImmunizationsRepository($connection);
        self::assertSame([], $repository->findByPid(123));
    }

    #[Test]
    public function findByPidMapsRowsToTypedShape(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 100,
                'cvx_code' => '140',
                'vaccine_name' => 'Influenza, seasonal, injectable, preservative free',
                'administered_date' => '2025-07-11',
                'manufacturer' => null,
                'lot_number' => null,
                'note' => null,
            ],
        ]);

        $repository = new ImmunizationsRepository($connection);
        $rows = $repository->findByPid(123);

        self::assertCount(1, $rows);
        self::assertSame(100, $rows[0]['id']);
        self::assertSame('140', $rows[0]['cvx_code']);
        self::assertSame(
            'Influenza, seasonal, injectable, preservative free',
            $rows[0]['vaccine_name']
        );
        self::assertSame('2025-07-11', $rows[0]['administered_date']);
        self::assertNull($rows[0]['manufacturer']);
        self::assertNull($rows[0]['lot_number']);
        self::assertNull($rows[0]['note']);
    }

    #[Test]
    public function findByPidCoercesEmptyStringsToNull(): void
    {
        // OpenEMR's legacy schema has several VARCHAR columns that are
        // NOT NULL DEFAULT '' on older schemas. The JSON contract
        // treats absence as null for downstream pydantic parsing.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => 7,
                'cvx_code' => '',
                'vaccine_name' => null,
                'administered_date' => null,
                'manufacturer' => '',
                'lot_number' => '',
                'note' => '',
            ],
        ]);

        $repository = new ImmunizationsRepository($connection);
        $rows = $repository->findByPid(123);

        self::assertNull($rows[0]['cvx_code']);
        self::assertNull($rows[0]['vaccine_name']);
        self::assertNull($rows[0]['administered_date']);
        self::assertNull($rows[0]['manufacturer']);
        self::assertNull($rows[0]['lot_number']);
        self::assertNull($rows[0]['note']);
    }

    #[Test]
    public function findByPidCoercesStringIdToInt(): void
    {
        // PDO drivers can return integer columns as strings depending on
        // driver configuration; the repository normalises that.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAllAssociative')->willReturn([
            [
                'id' => '42',
                'cvx_code' => '08',
                'vaccine_name' => 'Hep B, adolescent or pediatric',
                'administered_date' => '2010-01-15',
                'manufacturer' => null,
                'lot_number' => null,
                'note' => null,
            ],
        ]);

        $repository = new ImmunizationsRepository($connection);
        $rows = $repository->findByPid(1);

        self::assertSame(42, $rows[0]['id']);
    }
}
