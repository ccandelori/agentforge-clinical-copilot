<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Services\AgentForge;

use Doctrine\DBAL\Connection;
use Doctrine\DBAL\DriverManager;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Integration test: verifies UserRoleLookup's GACL query works against
 * the real OpenEMR schema and returns the expected group for the demo
 * admin user. Belongs to the regular (non-isolated) testsuite because
 * it touches MariaDB; the SQL contract is also unit-tested with mocks
 * in tests/Tests/Isolated/Modules/AgentForge/UserRoleLookupTest.php.
 *
 * The admin user's primary GACL group in stock OpenEMR demo data is
 * `admin` (group id 11). If this test breaks against a fresh demo
 * install the schema or seed data drifted, not the lookup logic.
 */
final class UserRoleLookupIntegrationTest extends TestCase
{
    private Connection $connection;

    protected function setUp(): void
    {
        // OpenEMR's tests/bootstrap.php loads interface/globals.php which
        // populates $GLOBALS['sqlconf'] from sites/default/sqlconf.php.
        // We construct a standalone DBAL connection rather than reaching
        // into OpenEMR's procedural ADODB layer; the sqlconf array is the
        // canonical source for DB credentials in tests.
        /** @var array{dbase: string, login: string, pass: string, host: string, port?: int|string} $sqlconf */
        $sqlconf = $GLOBALS['sqlconf'] ?? [];
        $port = $sqlconf['port'] ?? 3306;
        $this->connection = DriverManager::getConnection([
            'dbname' => $sqlconf['dbase'],
            'user' => $sqlconf['login'],
            'password' => $sqlconf['pass'],
            'host' => $sqlconf['host'],
            'port' => is_int($port) ? $port : (int) $port,
            'driver' => 'pdo_mysql',
        ]);
    }

    protected function tearDown(): void
    {
        $this->connection->close();
    }

    #[Test]
    public function findPrimaryGroupReturnsAdminGroupForAdminUser(): void
    {
        $sut = new UserRoleLookup($this->connection);

        self::assertSame('admin', $sut->findPrimaryGroup('admin'));
    }

    #[Test]
    public function findPrimaryGroupReturnsNullForNonexistentUser(): void
    {
        $sut = new UserRoleLookup($this->connection);

        self::assertNull(
            $sut->findPrimaryGroup('definitely-not-a-real-username-xyz')
        );
    }
}
