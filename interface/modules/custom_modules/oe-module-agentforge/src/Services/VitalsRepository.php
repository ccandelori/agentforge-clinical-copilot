<?php

declare(strict_types=1);

/**
 * VitalsRepository — typed read of form_vitals for the agent's
 * get_vitals_trend tool.
 *
 * MVP scope: returns the patient's recent vital sign measurements (BP,
 * pulse, respiration, temperature, SpO2, height, weight, BMI) over a
 * windowed time range. Reads via Doctrine DBAL directly so the connection
 * lifecycle matches the rest of the internal endpoints. See ARCHITECTURE.md
 * §4 (tool layer).
 *
 * Schema gotchas in form_vitals (sql/database.sql:2421) drive coercion
 * decisions in this class. They are documented here once so the SQL stays
 * a one-liner:
 *   - bps, bpd are VARCHAR(40) (not numeric) — they store strings like "120".
 *     We int-coerce; an empty string maps to null.
 *   - All numeric vitals (weight, height, temperature, pulse, respiration,
 *     BMI, oxygen_saturation) are DECIMAL with default '0.00'. A real-world
 *     weight or temperature of literally 0 doesn't exist clinically, so
 *     0.0 is treated as "not recorded" and surfaced as null. This keeps the
 *     LLM from second-guessing "0 systolic" or "patient weighs 0 lb."
 *   - BMI is uppercase in the column name.
 *
 * The query uses the idx_form_vitals_pid_date index added by Task 40.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use DateTimeImmutable;
use Doctrine\DBAL\Connection;

class VitalsRepository
{
    private const ROW_LIMIT = 200;
    private const MIN_DAYS = 1;
    private const MAX_DAYS = 730;

    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return list<array{
     *     id: int,
     *     date: string|null,
     *     systolic: int|null,
     *     diastolic: int|null,
     *     pulse: float|null,
     *     respiration: float|null,
     *     temperature: float|null,
     *     temp_method: string|null,
     *     oxygen_saturation: float|null,
     *     height: float|null,
     *     weight: float|null,
     *     bmi: float|null,
     *     bmi_status: string|null,
     *     note: string|null
     * }>
     */
    public function findRecentByPid(int $pid, int $days = 90): array
    {
        $clampedDays = max(self::MIN_DAYS, min(self::MAX_DAYS, $days));
        $since = (new DateTimeImmutable())
            ->modify("-{$clampedDays} days")
            ->format('Y-m-d H:i:s');

        $rows = $this->connection->fetchAllAssociative(
            "SELECT id, `date`, bps, bpd, pulse, respiration, temperature,
                    temp_method, oxygen_saturation, height, weight, BMI,
                    BMI_status, note
             FROM form_vitals
             WHERE pid = :pid AND `date` >= :since
             ORDER BY `date` DESC
             LIMIT " . self::ROW_LIMIT,
            ['pid' => $pid, 'since' => $since]
        );

        $result = [];
        foreach ($rows as $row) {
            $idRaw = $row['id'] ?? null;
            $dateRaw = $row['date'] ?? null;
            $tempMethodRaw = $row['temp_method'] ?? null;
            $bmiStatusRaw = $row['BMI_status'] ?? null;
            $noteRaw = $row['note'] ?? null;

            $result[] = [
                'id' => is_int($idRaw) ? $idRaw : (is_string($idRaw) ? (int) $idRaw : 0),
                'date' => is_string($dateRaw) && $dateRaw !== '' ? $dateRaw : null,
                'systolic' => $this->stringToIntOrNull($row['bps'] ?? null),
                'diastolic' => $this->stringToIntOrNull($row['bpd'] ?? null),
                'pulse' => $this->decimalToFloatOrNull($row['pulse'] ?? null),
                'respiration' => $this->decimalToFloatOrNull($row['respiration'] ?? null),
                'temperature' => $this->decimalToFloatOrNull($row['temperature'] ?? null),
                'temp_method' => is_string($tempMethodRaw) && $tempMethodRaw !== ''
                    ? $tempMethodRaw : null,
                'oxygen_saturation' => $this->decimalToFloatOrNull(
                    $row['oxygen_saturation'] ?? null
                ),
                'height' => $this->decimalToFloatOrNull($row['height'] ?? null),
                'weight' => $this->decimalToFloatOrNull($row['weight'] ?? null),
                'bmi' => $this->decimalToFloatOrNull($row['BMI'] ?? null),
                'bmi_status' => is_string($bmiStatusRaw) && $bmiStatusRaw !== ''
                    ? $bmiStatusRaw : null,
                'note' => is_string($noteRaw) && $noteRaw !== '' ? $noteRaw : null,
            ];
        }
        return $result;
    }

    private function stringToIntOrNull(mixed $value): ?int
    {
        if (is_int($value)) {
            return $value !== 0 ? $value : null;
        }
        if (!is_string($value) || $value === '') {
            return null;
        }
        if (!is_numeric($value)) {
            return null;
        }
        $coerced = (int) $value;
        return $coerced !== 0 ? $coerced : null;
    }

    private function decimalToFloatOrNull(mixed $value): ?float
    {
        if (is_float($value) || is_int($value)) {
            $f = (float) $value;
            return $f !== 0.0 ? $f : null;
        }
        if (!is_string($value) || $value === '') {
            return null;
        }
        if (!is_numeric($value)) {
            return null;
        }
        $f = (float) $value;
        return $f !== 0.0 ? $f : null;
    }
}
