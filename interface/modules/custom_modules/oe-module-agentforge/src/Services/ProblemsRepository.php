<?php

declare(strict_types=1);

/**
 * ProblemsRepository — typed read of the lists table for the agent's
 * get_active_problems tool.
 *
 * MVP scope: returns the patient's currently active problem-list entries
 * (activity=1, type='medical_problem') with optional diagnosis code and
 * begin date. Reads via Doctrine DBAL so the connection lifecycle matches
 * the rest of the internal endpoints. See ARCHITECTURE.md §4 (tool layer).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class ProblemsRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return array<int, array{id: int, title: string, diagnosis: string|null, begin_date: string|null}>
     */
    public function findActiveByPid(int $pid): array
    {
        // DATE(begdate) strips the time component. lists.begdate is
        // a DATETIME column and Synthea-imported rows carry real
        // timestamps; without the cast the JSON ships
        // "2026-02-06 17:32:52" and the sidecar's pydantic
        // ``date | None`` field rejects it as "datetime should have
        // zero time". The cast is the wire-format contract — the
        // sidecar trusts every begin_date to be a YYYY-MM-DD string.
        $rows = $this->connection->fetchAllAssociative(
            "SELECT id, title, diagnosis, DATE(begdate) AS begdate
             FROM lists
             WHERE pid = :pid AND type = 'medical_problem' AND activity = 1
             ORDER BY begdate DESC, id DESC",
            ['pid' => $pid]
        );

        $result = [];
        foreach ($rows as $row) {
            $id = $row['id'] ?? null;
            $title = $row['title'] ?? null;
            $diagnosis = $row['diagnosis'] ?? null;
            $beg = $row['begdate'] ?? null;
            $result[] = [
                'id' => is_int($id) ? $id : (is_string($id) ? (int) $id : 0),
                'title' => is_string($title) ? $title : '',
                'diagnosis' => is_string($diagnosis) && $diagnosis !== '' ? $diagnosis : null,
                'begin_date' => is_string($beg) && $beg !== '' ? $beg : null,
            ];
        }
        return $result;
    }
}
