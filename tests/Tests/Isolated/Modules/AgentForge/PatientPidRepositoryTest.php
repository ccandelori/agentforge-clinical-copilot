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
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for PatientPidRepository — resolves a FHIR Patient
 * resource UUID into the integer ``patient_data.pid`` the agent's
 * internal JWT contract requires. See ADR-0001 §5 for why this is a
 * separate hop from /me (user identity vs patient context).
 */
final class PatientPidRepositoryTest extends TestCase
{
    #[Test]
    public function findPidByUuidReturnsIntegerForKnownUuid(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchOne')
            ->with(
                self::stringContains('patient_data'),
                ['8a7b6c5d-aaaa-bbbb-cccc-dddddddddddd']
            )
            ->willReturn(42);

        $sut = new PatientPidRepository($connection);

        self::assertSame(42, $sut->findPidByUuid('8a7b6c5d-aaaa-bbbb-cccc-dddddddddddd'));
    }

    #[Test]
    public function findPidByUuidCoercesStringPidToInt(): void
    {
        // Doctrine sometimes returns INT columns as strings depending
        // on the driver — we should coerce defensively.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('17');

        $sut = new PatientPidRepository($connection);

        self::assertSame(17, $sut->findPidByUuid('any-uuid'));
    }

    #[Test]
    public function findPidByUuidReturnsNullForUnknownUuid(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn(false);

        $sut = new PatientPidRepository($connection);

        self::assertNull($sut->findPidByUuid('nope'));
    }

    #[Test]
    public function findPidByUuidReturnsNullWhenColumnIsNonNumeric(): void
    {
        // Defensive: malformed row → 404 upstream rather than a
        // partial/wrong identity.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('not-a-number');

        $sut = new PatientPidRepository($connection);

        self::assertNull($sut->findPidByUuid('any'));
    }

    #[Test]
    public function findPidByUuidUsesParameterizedQueryAgainstUuidColumn(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchOne')
            ->with(
                self::callback(static function (string $sql): bool {
                    return str_contains($sql, 'patient_data')
                        && str_contains($sql, 'uuid')
                        && str_contains($sql, 'pid');
                }),
                self::isType('array'),
            )
            ->willReturn(1);

        $sut = new PatientPidRepository($connection);
        $sut->findPidByUuid('any');
    }
}
