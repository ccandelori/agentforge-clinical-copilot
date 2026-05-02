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
use OpenEMR\Modules\AgentForge\Services\EncountersRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for EncountersRepository.
 *
 * The repository serves the agent's get_recent_encounters tool by reading
 * form_encounter rows for a patient, joined to users for provider names.
 * Tests pin the SQL shape (FROM/JOIN/WHERE/ORDER BY/LIMIT, parameter
 * binding) and the row-normalization rules the agent will see.
 */
final class EncountersRepositoryTest extends TestCase
{
    #[Test]
    public function findRecentByPidQueriesFormEncounterWithProviderJoin(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(123, 30);

        self::assertStringContainsString('FROM form_encounter', $captured['sql']);
        self::assertStringContainsString('LEFT JOIN users', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidScopesToPidAndDateWindow(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(1, 30);

        self::assertStringContainsString('WHERE', $captured['sql']);
        self::assertStringContainsString('pid', $captured['sql']);
        // The date filter is on the encounter's `date` column.
        self::assertStringContainsString('`date` >=', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidOrdersByDateDescAndLimits(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(1, 30);

        self::assertStringContainsString('ORDER BY', $captured['sql']);
        self::assertStringContainsString('DESC', $captured['sql']);
        self::assertStringContainsString('LIMIT 50', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidBindsPidAndSinceParameters(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(123, 30);

        $params = $captured['params'];
        self::assertSame(123, $params['pid']);
        self::assertIsString($params['since']);
        self::assertMatchesRegularExpression(
            '/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/',
            $params['since'],
        );
    }

    #[Test]
    public function findRecentByPidClampsExcessiveDaysToMaximum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(1, 99999);

        // The clamp should cap at 730 days. Since timestamp should be no
        // earlier than that.
        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        $maxLookbackSeconds = 730 * 86400;
        self::assertGreaterThanOrEqual(
            time() - $maxLookbackSeconds - 60,
            $sinceTimestamp,
        );
    }

    #[Test]
    public function findRecentByPidClampsZeroOrNegativeDaysToMinimum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new EncountersRepository($connection);

        $repository->findRecentByPid(1, 0);

        // Zero days clamps to 1 — since is roughly "yesterday."
        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        self::assertLessThan(time(), $sinceTimestamp);
        self::assertGreaterThan(time() - 86400 - 60, $sinceTimestamp);
    }

    #[Test]
    public function encounterRowsAreNormalizedToTypedShape(): void
    {
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow([
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'reason' => 'follow-up for diabetes',
                'encounter_type' => 'Office Visit',
                'class_code' => 'AMB',
                'provider_id' => 12,
                'provider_name' => 'dr.smith',
                'sensitivity' => null,
                'encounter_category' => 5,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertCount(1, $rows);
        self::assertSame(5, $rows[0]['id']);
        self::assertSame('2026-04-20 14:30:00', $rows[0]['date']);
        self::assertSame('follow-up for diabetes', $rows[0]['reason']);
        self::assertSame('Office Visit', $rows[0]['encounter_type']);
        self::assertSame('AMB', $rows[0]['class_code']);
        self::assertSame(12, $rows[0]['provider_id']);
        self::assertSame('dr.smith', $rows[0]['provider_name']);
        self::assertNull($rows[0]['sensitivity']);
        self::assertSame(5, $rows[0]['encounter_category']);
    }

    #[Test]
    public function emptyStringTextFieldsBecomeNull(): void
    {
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow([
                'reason' => '',
                'encounter_type' => '',
                'class_code' => '',
                'provider_name' => '',
                'sensitivity' => '',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['reason']);
        self::assertNull($rows[0]['encounter_type']);
        self::assertNull($rows[0]['class_code']);
        self::assertNull($rows[0]['provider_name']);
        self::assertNull($rows[0]['sensitivity']);
    }

    #[Test]
    public function nullTextFieldsStayNull(): void
    {
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow([
                'reason' => null,
                'encounter_type' => null,
                'class_code' => null,
                'provider_name' => null,
                'sensitivity' => null,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['reason']);
        self::assertNull($rows[0]['encounter_type']);
        self::assertNull($rows[0]['class_code']);
        self::assertNull($rows[0]['provider_name']);
        self::assertNull($rows[0]['sensitivity']);
    }

    #[Test]
    public function providerIdOfZeroBecomesNull(): void
    {
        // form_encounter.provider_id defaults to 0 (legacy "no provider"
        // sentinel). Normalize that to null so the agent sees a clean
        // missing-provider signal alongside the LEFT JOIN's null.
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow([
                'provider_id' => 0,
                'provider_name' => null,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['provider_id']);
        self::assertNull($rows[0]['provider_name']);
    }

    #[Test]
    public function returnsEmptyArrayWhenNoRowsFound(): void
    {
        $repository = new EncountersRepository($this->makeConnection([]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame([], $rows);
    }

    #[Test]
    public function preservesSensitivityWhenNonEmpty(): void
    {
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow(['sensitivity' => 'normal']),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame('normal', $rows[0]['sensitivity']);
    }

    #[Test]
    public function encounterCategoryPassesThroughAsIntIncludingZero(): void
    {
        // pc_catid is a NOT NULL int — 0 is a legitimate (if unusual)
        // category id and should not be coerced to null. Default is 5.
        $repository = new EncountersRepository($this->makeConnection([
            $this->fixtureRow(['encounter_category' => 0]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame(0, $rows[0]['encounter_category']);
    }

    /**
     * Build a fixture row matching the SELECT-alias column shape.
     *
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     */
    private function fixtureRow(array $overrides = []): array
    {
        $defaults = [
            'id' => 1,
            'date' => '2026-04-20 14:30:00',
            'reason' => 'follow-up',
            'encounter_type' => 'Office Visit',
            'class_code' => 'AMB',
            'provider_id' => 12,
            'provider_name' => 'dr.smith',
            'sensitivity' => null,
            'encounter_category' => 5,
        ];
        return array_merge($defaults, $overrides);
    }

    /**
     * @param list<array<string, mixed>> $rows
     * @param array<string, mixed>       $captured
     */
    private function makeConnection(array $rows, array &$captured = []): Connection
    {
        $captured = ['sql' => '', 'params' => []];

        $connection = self::createMock(Connection::class);
        $connection
            ->method('fetchAllAssociative')
            ->willReturnCallback(function (string $sql, array $params) use (
                &$captured,
                $rows
            ): array {
                $captured['sql'] = $sql;
                $captured['params'] = $params;
                return $rows;
            });
        return $connection;
    }
}
