<?php

declare(strict_types=1);

/**
 * MedicationsRepository — typed read of the lists table for the agent's
 * get_active_medications tool.
 *
 * MVP scope: returns the patient's currently active medication entries
 * (activity=1, type='medication') with optional begin/end dates. Reads
 * via Doctrine DBAL so the connection lifecycle matches the rest of the
 * internal endpoints. See ARCHITECTURE.md §4 (tool layer).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class MedicationsRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return array<int, array{id: int, name: string, begin_date: string|null, end_date: string|null}>
     */
    public function findActiveByPid(int $pid): array
    {
        // DATE() strips the time component from lists.begdate /
        // lists.enddate (both DATETIME columns). Synthea-imported rows
        // carry real timestamps, and the sidecar's pydantic
        // ``date | None`` fields reject datetime strings with non-zero
        // time. The cast is the wire-format contract — every
        // begin_date / end_date ships as a YYYY-MM-DD string.
        $rows = $this->connection->fetchAllAssociative(
            "SELECT id, title,
                    DATE(begdate) AS begdate,
                    DATE(enddate) AS enddate
             FROM lists
             WHERE pid = :pid AND type = 'medication' AND activity = 1
             ORDER BY begdate DESC, id DESC",
            ['pid' => $pid]
        );

        $result = [];
        foreach ($rows as $row) {
            $id = $row['id'] ?? null;
            $title = $row['title'] ?? null;
            $beg = $row['begdate'] ?? null;
            $end = $row['enddate'] ?? null;
            $result[] = [
                'id' => is_int($id) ? $id : (is_string($id) ? (int) $id : 0),
                'name' => is_string($title) ? $title : '',
                'begin_date' => is_string($beg) && $beg !== '' ? $beg : null,
                'end_date' => is_string($end) && $end !== '' ? $end : null,
            ];
        }
        return $result;
    }
}
