<?php

/**
 * LabsRepository — typed read of the procedure_order/report/result triple
 * for the agent's get_recent_labs tool.
 *
 * MVP scope: returns the patient's recent lab analytes (joined across
 * procedure_order → procedure_report → procedure_result) as a flattened
 * list, newest first, capped at 200 rows. Reads via Doctrine DBAL so the
 * connection lifecycle matches the rest of the internal endpoints. See
 * ARCHITECTURE.md §4 (tool layer).
 *
 * The 200-row cap is a safety net against panel-heavy patients (e.g. a
 * BMP + CMP + CBC inside one window will easily emit 30+ analytes per
 * report, and the LLM's context window is the binding constraint).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use DateTimeImmutable;
use Doctrine\DBAL\Connection;
use Psr\Clock\ClockInterface;

class LabsRepository
{
    private const MAX_ROWS = 200;

    public function __construct(
        private readonly Connection $connection,
        private readonly ClockInterface $clock,
    ) {
    }

    /**
     * @return array<int, array{
     *     id: int,
     *     order_id: int,
     *     report_id: int,
     *     test_code: string|null,
     *     test_name: string|null,
     *     value: string|null,
     *     units: string|null,
     *     reference_range: string|null,
     *     abnormal: string|null,
     *     date: string|null
     * }>
     */
    public function findRecentByPid(int $pid, int $days = 90): array
    {
        $since = $this->clock->now()
            ->sub(new \DateInterval('P' . $days . 'D'))
            ->format('Y-m-d 00:00:00');

        // DATE(po.date_ordered) strips the time component. The column
        // is DATETIME and Synthea-imported orders carry real
        // timestamps; without the cast the JSON ships
        // "2026-04-15 09:30:00" and the sidecar's pydantic
        // ``date | None`` field rejects datetime strings with
        // non-zero time. Same wire-format contract as the lists-table
        // repos (problems / medications / allergies).
        $sql = "SELECT
                    pres.procedure_result_id  AS id,
                    po.procedure_order_id     AS order_id,
                    pr.procedure_report_id    AS report_id,
                    pres.result_code          AS test_code,
                    pres.result_text          AS test_name,
                    pres.result               AS value,
                    pres.units                AS units,
                    pres.range                AS reference_range,
                    pres.abnormal             AS abnormal,
                    DATE(po.date_ordered)     AS date
                FROM procedure_order po
                INNER JOIN procedure_report pr
                    ON pr.procedure_order_id = po.procedure_order_id
                INNER JOIN procedure_result pres
                    ON pres.procedure_report_id = pr.procedure_report_id
                WHERE po.patient_id = :pid
                  AND po.date_ordered >= :since
                ORDER BY po.date_ordered DESC,
                         pr.procedure_report_id DESC,
                         pres.procedure_result_id ASC
                LIMIT " . self::MAX_ROWS;

        $rows = $this->connection->fetchAllAssociative(
            $sql,
            ['pid' => $pid, 'since' => $since],
        );

        $result = [];
        foreach ($rows as $row) {
            $result[] = [
                'id' => self::asInt($row['id'] ?? null),
                'order_id' => self::asInt($row['order_id'] ?? null),
                'report_id' => self::asInt($row['report_id'] ?? null),
                'test_code' => self::asNullableString($row['test_code'] ?? null),
                'test_name' => self::asNullableString($row['test_name'] ?? null),
                'value' => self::asNullableString($row['value'] ?? null),
                'units' => self::asNullableString($row['units'] ?? null),
                'reference_range' => self::asNullableString($row['reference_range'] ?? null),
                'abnormal' => self::asNullableString($row['abnormal'] ?? null),
                'date' => self::asNullableDate($row['date'] ?? null),
            ];
        }
        return $result;
    }

    private static function asInt(mixed $value): int
    {
        if (is_int($value)) {
            return $value;
        }
        if (is_string($value)) {
            return (int) $value;
        }
        return 0;
    }

    private static function asNullableString(mixed $value): ?string
    {
        if (!is_string($value) || $value === '') {
            return null;
        }
        return $value;
    }

    /**
     * MariaDB returns datetimes as 'YYYY-MM-DD HH:MM:SS' strings; the agent
     * only needs the date part for trend reasoning, so we trim to the date
     * and let the Python tool parse it as a date (not datetime).
     */
    private static function asNullableDate(mixed $value): ?string
    {
        if (!is_string($value) || $value === '') {
            return null;
        }
        // Pre-validate via strtotime() to avoid the try/catch entirely.
        // DateTimeImmutable throws \Exception on bad input (PHP 8.2) or
        // \DateMalformedStringException on PHP 8.3+, both of which would
        // require catching a base type the project's ForbiddenCatchTypeRule
        // forbids (because \Exception catches \ErrorException). strtotime
        // returns false on bad input — no exception, no catch needed.
        $timestamp = strtotime($value);
        if ($timestamp === false) {
            return null;
        }
        return (new DateTimeImmutable('@' . $timestamp))->format('Y-m-d');
    }
}
