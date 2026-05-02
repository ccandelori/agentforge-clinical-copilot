<?php

declare(strict_types=1);

/**
 * ProceduresRepository — typed read of the procedure_order table for the
 * agent's get_procedures tool.
 *
 * Procedures share storage with labs (both live in procedure_order). The
 * clinical separator is whether the order produced any procedure_result
 * rows: orders with results ARE labs (BMP, CBC, A1c — values with units
 * and reference ranges); orders without results are true procedures
 * (PHQ-9 screenings, AUDIT, IPV screens, surgical referrals, dental
 * cleanings). The SQL filters out result-bearing orders so the procedures
 * view doesn't duplicate the labs view.
 *
 * Synthea generates an annual recurrence pattern for many screenings,
 * so a single SNOMED code (e.g. depression screening) can repeat 6+
 * times for a long-term patient. The ROW_NUMBER dedup collapses to one
 * row per code (most recent first) — same trick as ProblemsRepository.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;
use Psr\Clock\ClockInterface;

class ProceduresRepository
{
    private const MAX_ROWS = 100;

    public function __construct(
        private readonly Connection $connection,
        private readonly ClockInterface $clock,
    ) {
    }

    /**
     * @return array<int, array{
     *     id: int,
     *     procedure_code: string|null,
     *     procedure_name: string|null,
     *     date_ordered: string|null,
     *     status: string|null,
     *     encounter_id: int|null
     * }>
     */
    public function findRecentByPid(int $pid, int $days = 365): array
    {
        $since = $this->clock->now()
            ->sub(new \DateInterval('P' . $days . 'D'))
            ->format('Y-m-d 00:00:00');

        // procedure_name lives on procedure_order_code (one row per code
        // per order). For Synthea-imported data there's exactly one
        // procedure_order_code row per order, so a straight join is
        // safe; if a future source emits multiple codes per order the
        // dedup below still keeps one row per code.
        //
        // ROW_NUMBER PARTITION BY procedure_code keeps the most recent
        // occurrence of each procedure code. Annual depression
        // screenings collapse from 6 rows to 1.
        //
        // NOT EXISTS against procedure_result is the lab/procedure
        // separator — orders that produced numeric analytes are labs
        // and surface through the get_recent_labs tool, not here.
        //
        // DATE() strips the time component for the wire-format
        // contract (sidecar pydantic ``date | None``).
        $sql = "SELECT id, procedure_code, procedure_name, date_ordered, status, encounter_id
                FROM (
                    SELECT
                        po.procedure_order_id          AS id,
                        poc.procedure_code             AS procedure_code,
                        poc.procedure_name             AS procedure_name,
                        DATE(po.date_ordered)          AS date_ordered,
                        po.order_status                AS status,
                        po.encounter_id                AS encounter_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY poc.procedure_code
                            ORDER BY po.date_ordered DESC, po.procedure_order_id DESC
                        ) AS rn
                    FROM procedure_order po
                    JOIN procedure_order_code poc
                        ON poc.procedure_order_id = po.procedure_order_id
                    WHERE po.patient_id = :pid
                      AND po.date_ordered >= :since
                      AND po.order_status = 'completed'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM procedure_report pr
                          JOIN procedure_result pres
                              ON pres.procedure_report_id = pr.procedure_report_id
                          WHERE pr.procedure_order_id = po.procedure_order_id
                      )
                ) ranked
                WHERE rn = 1
                ORDER BY date_ordered DESC, id DESC
                LIMIT " . self::MAX_ROWS;

        $rows = $this->connection->fetchAllAssociative(
            $sql,
            ['pid' => $pid, 'since' => $since],
        );

        $result = [];
        foreach ($rows as $row) {
            $result[] = [
                'id' => self::asInt($row['id'] ?? null),
                'procedure_code' => self::asNullableString($row['procedure_code'] ?? null),
                'procedure_name' => self::asNullableString($row['procedure_name'] ?? null),
                'date_ordered' => self::asNullableString($row['date_ordered'] ?? null),
                'status' => self::asNullableString($row['status'] ?? null),
                'encounter_id' => self::asNullableInt($row['encounter_id'] ?? null),
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

    /**
     * encounter_id is `bigint NOT NULL DEFAULT 0` in the legacy schema —
     * 0 is the "no encounter" sentinel. We treat that the same as null
     * at the JSON boundary so the agent doesn't see a meaningless 0.
     */
    private static function asNullableInt(mixed $value): ?int
    {
        if (is_int($value)) {
            return $value === 0 ? null : $value;
        }
        if (is_string($value) && $value !== '') {
            $n = (int) $value;
            return $n === 0 ? null : $n;
        }
        return null;
    }

    private static function asNullableString(mixed $value): ?string
    {
        if (!is_string($value) || $value === '') {
            return null;
        }
        return $value;
    }
}
