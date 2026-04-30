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
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for UserRoleLookup — verifies the SQL contract and the
 * null-on-unknown-user behavior using a mocked Doctrine Connection.
 *
 * The actual SQL is exercised against the real GACL schema in the
 * integration test (tests/Tests/Services/AgentForge/UserRoleLookupIntegrationTest.php).
 */
final class UserRoleLookupTest extends TestCase
{
    #[Test]
    public function findPrimaryGroupReturnsGroupNameForKnownUser(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchOne')
            ->with(
                self::stringContains('gacl_aro'),
                ['admin']
            )
            ->willReturn('Administrators');

        $sut = new UserRoleLookup($connection);

        self::assertSame('Administrators', $sut->findPrimaryGroup('admin'));
    }

    #[Test]
    public function findPrimaryGroupReturnsNullWhenUserHasNoGroup(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchOne')
            ->willReturn(false);

        $sut = new UserRoleLookup($connection);

        self::assertNull($sut->findPrimaryGroup('unknown_user'));
    }

    #[Test]
    public function findPrimaryGroupOrdersByGroupIdAscendingForDeterministicResult(): void
    {
        // Documents the contract: when a user has multiple groups, the
        // lookup returns the one with the lowest gacl_aro_groups.id.
        // Captured here so a future refactor that drops ORDER BY breaks
        // a test rather than silently changing behavior.
        $connection = self::createMock(Connection::class);
        $captured = '';
        $connection->expects(self::once())
            ->method('fetchOne')
            ->willReturnCallback(function (string $sql) use (&$captured) {
                $captured = $sql;
                return 'Physicians';
            });

        $sut = new UserRoleLookup($connection);
        $sut->findPrimaryGroup('any');

        self::assertStringContainsString('ORDER BY grp.id ASC', $captured);
        self::assertStringContainsString('LIMIT 1', $captured);
    }
}
