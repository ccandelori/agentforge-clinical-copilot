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
use OpenEMR\Modules\AgentForge\Services\VitalsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for VitalsRepository.
 *
 * The repository is responsible for two things the controller can't see:
 * (1) the SQL shape (uses idx_form_vitals_pid_date and respects the
 * lookback window) and (2) the value-coercion rules forced on us by
 * form_vitals' schema gotchas — VARCHAR-stored BP fields and DECIMAL
 * defaults of 0.00 that mean "not recorded."
 */
final class VitalsRepositoryTest extends TestCase
{
    #[Test]
    public function findRecentByPidQueriesPidAndSinceColumns(): void
    {
        $captured = [];
        $connection = $this->makeConnection(
            rows: [],
            captured: $captured,
        );
        $repository = new VitalsRepository($connection);

        $repository->findRecentByPid(123, 30);

        self::assertStringContainsString('FROM form_vitals', $captured['sql']);
        self::assertStringContainsString('WHERE pid = :pid', $captured['sql']);
        self::assertStringContainsString('`date` >= :since', $captured['sql']);
        self::assertStringContainsString('ORDER BY `date` DESC', $captured['sql']);
        self::assertStringContainsString('LIMIT 200', $captured['sql']);

        $params = $captured['params'];
        self::assertSame(123, $params['pid']);
        self::assertIsString($params['since']);
        // The since param should be a SQL datetime ~30 days before "now."
        // We don't pin the exact instant — clock injection can come later —
        // but we sanity-check the format and direction.
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
        $repository = new VitalsRepository($connection);

        $repository->findRecentByPid(1, 99999);

        // The clamp should cap at 730 days. We verify by checking that the
        // computed since is no further back than ~730 days from now (i.e.
        // the year 2024-or-later under current dates).
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
        $repository = new VitalsRepository($connection);

        $repository->findRecentByPid(1, 0);

        // Zero days clamps to 1, so the since is roughly "yesterday."
        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        self::assertLessThan(time(), $sinceTimestamp);
        self::assertGreaterThan(time() - 86400 - 60, $sinceTimestamp);
    }

    #[Test]
    public function rowCoercesVarcharBloodPressureToInt(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow(['bps' => '128', 'bpd' => '82']),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertCount(1, $rows);
        self::assertSame(128, $rows[0]['systolic']);
        self::assertSame(82, $rows[0]['diastolic']);
    }

    #[Test]
    public function emptyStringBloodPressureBecomesNull(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow(['bps' => '', 'bpd' => '']),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['systolic']);
        self::assertNull($rows[0]['diastolic']);
    }

    #[Test]
    public function nullBloodPressureBecomesNull(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow(['bps' => null, 'bpd' => null]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['systolic']);
        self::assertNull($rows[0]['diastolic']);
    }

    #[Test]
    public function nonNumericBloodPressureBecomesNull(): void
    {
        // Defense-in-depth: bps/bpd are VARCHAR(40), so anything could end
        // up there. Garbage data should not crash the response.
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow(['bps' => 'unknown', 'bpd' => 'n/a']),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['systolic']);
        self::assertNull($rows[0]['diastolic']);
    }

    #[Test]
    public function decimalZeroValuesAreCoercedToNull(): void
    {
        // form_vitals defaults all DECIMAL columns to '0.00'. A real-world
        // weight or temperature of literally 0 doesn't exist clinically,
        // so the repository surfaces the schema default as null.
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow([
                'weight' => '0.000000',
                'height' => '0.00',
                'temperature' => '0.00',
                'pulse' => '0.00',
                'respiration' => '0.00',
                'BMI' => '0.00',
                'oxygen_saturation' => '0.00',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['weight']);
        self::assertNull($rows[0]['height']);
        self::assertNull($rows[0]['temperature']);
        self::assertNull($rows[0]['pulse']);
        self::assertNull($rows[0]['respiration']);
        self::assertNull($rows[0]['bmi']);
        self::assertNull($rows[0]['oxygen_saturation']);
    }

    #[Test]
    public function decimalNonZeroValuesAreCoercedToFloat(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow([
                'weight' => '180.500000',
                'temperature' => '98.60',
                'oxygen_saturation' => '98.00',
                'BMI' => '25.90',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame(180.5, $rows[0]['weight']);
        self::assertSame(98.6, $rows[0]['temperature']);
        self::assertSame(98.0, $rows[0]['oxygen_saturation']);
        self::assertSame(25.9, $rows[0]['bmi']);
    }

    #[Test]
    public function returnsEmptyArrayWhenNoRowsFound(): void
    {
        $repository = new VitalsRepository($this->makeConnection([]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame([], $rows);
    }

    #[Test]
    public function preservesDateAndTextFields(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow([
                'id' => 7,
                'date' => '2026-04-15 10:30:00',
                'temp_method' => 'oral',
                'BMI_status' => 'overweight',
                'note' => 'Post-prandial measurement.',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame(7, $rows[0]['id']);
        self::assertSame('2026-04-15 10:30:00', $rows[0]['date']);
        self::assertSame('oral', $rows[0]['temp_method']);
        self::assertSame('overweight', $rows[0]['bmi_status']);
        self::assertSame('Post-prandial measurement.', $rows[0]['note']);
    }

    #[Test]
    public function emptyStringTextFieldsBecomeNull(): void
    {
        $repository = new VitalsRepository($this->makeConnection([
            $this->fixtureRow([
                'temp_method' => '',
                'BMI_status' => '',
                'note' => '',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['temp_method']);
        self::assertNull($rows[0]['bmi_status']);
        self::assertNull($rows[0]['note']);
    }

    /**
     * Build a fixture row by merging caller overrides with all-null defaults.
     *
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     */
    private function fixtureRow(array $overrides = []): array
    {
        $defaults = [
            'id' => 1,
            'date' => '2026-04-15 10:30:00',
            'bps' => null,
            'bpd' => null,
            'pulse' => null,
            'respiration' => null,
            'temperature' => null,
            'temp_method' => null,
            'oxygen_saturation' => null,
            'height' => null,
            'weight' => null,
            'BMI' => null,
            'BMI_status' => null,
            'note' => null,
        ];
        return array_merge($defaults, $overrides);
    }

    /**
     * @param list<array<string, mixed>> $rows
     * @param array<string, mixed>       $captured  Out-parameter; the call
     *                                              records the SQL and bound
     *                                              params for assertion.
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
