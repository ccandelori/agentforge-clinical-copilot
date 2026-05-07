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
use OpenEMR\Modules\AgentForge\Services\UserIdentity;
use OpenEMR\Modules\AgentForge\Services\UserIdentityRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for UserIdentityRepository — verifies the UUID → user_id
 * + username resolution that the dashboard auth bridge (ADR-0001)
 * needs to mint internal JWTs from an OIDC session.
 *
 * The actual SQL is exercised against the real `users` schema in any
 * eventual integration test; here we assert the prepared-statement
 * contract using a mocked Doctrine Connection.
 */
final class UserIdentityRepositoryTest extends TestCase
{
    #[Test]
    public function findByUuidReturnsUserIdentityForKnownUuid(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAssociative')
            ->with(
                self::stringContains('users'),
                ['8a7b6c5d-1234-5678-9abc-def012345678']
            )
            ->willReturn([
                'id' => 17,
                'username' => 'admin',
            ]);

        $sut = new UserIdentityRepository($connection);
        $identity = $sut->findByUuid('8a7b6c5d-1234-5678-9abc-def012345678');

        self::assertInstanceOf(UserIdentity::class, $identity);
        self::assertSame(17, $identity->userId);
        self::assertSame('admin', $identity->username);
    }

    #[Test]
    public function findByUuidReturnsNullWhenUuidIsNotFound(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAssociative')
            ->willReturn(false);

        $sut = new UserIdentityRepository($connection);

        self::assertNull(
            $sut->findByUuid('nonexistent-uuid-0000-0000-000000000000'),
        );
    }

    #[Test]
    public function findByUuidUsesParameterizedQueryForTheUuidColumn(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::once())
            ->method('fetchAssociative')
            ->with(
                self::callback(static function (string $sql): bool {
                    // Must filter by users.uuid (not users.id) and select
                    // both id and username — the JWT minter needs both.
                    return str_contains($sql, 'users')
                        && str_contains($sql, 'uuid')
                        && str_contains($sql, 'id')
                        && str_contains($sql, 'username');
                }),
                self::isType('array'),
            )
            ->willReturn(['id' => 1, 'username' => 'admin']);

        $sut = new UserIdentityRepository($connection);
        $sut->findByUuid('any-uuid');
    }

    #[Test]
    public function findByUuidReturnsNullWhenIdColumnIsMissing(): void
    {
        // Defensive: a malformed row (missing id) shouldn't blow up the
        // bridge — degrade to "uuid not found" so the caller responds 404.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAssociative')->willReturn(['username' => 'admin']);

        $sut = new UserIdentityRepository($connection);

        self::assertNull($sut->findByUuid('any-uuid'));
    }

    #[Test]
    public function findByUuidReturnsNullWhenUsernameColumnIsMissing(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAssociative')->willReturn(['id' => 5]);

        $sut = new UserIdentityRepository($connection);

        self::assertNull($sut->findByUuid('any-uuid'));
    }
}
