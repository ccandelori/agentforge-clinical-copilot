<?php

declare(strict_types=1);

/**
 * AllergiesRepository — typed read of the lists table for the agent's
 * get_active_allergies tool.
 *
 * MVP scope: returns the patient's currently active allergy entries
 * (activity=1, type='allergy') with reaction text, severity code, and
 * optional begin/end dates. Reads via Doctrine DBAL so the connection
 * lifecycle matches the rest of the internal endpoints. See
 * ARCHITECTURE.md §4 (tool layer).
 *
 * The empty-string `reaction` value (a NOT NULL DEFAULT '' column in the
 * legacy schema) is normalised to null so downstream JSON consumers see
 * the absence of data as null rather than the empty string.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class AllergiesRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return array<int, array{
     *     id: int,
     *     name: string,
     *     reaction: string|null,
     *     severity: string|null,
     *     begin_date: string|null,
     *     end_date: string|null
     * }>
     */
    public function findActiveByPid(int $pid): array
    {
        // DATE() strips the time component from lists.begdate /
        // lists.enddate (both DATETIME columns). Allergies happened to
        // import with midnight timestamps (Synthea modeling), so this
        // never surfaced as a bug — but the sidecar's pydantic
        // ``date | None`` field would reject any non-zero-time row.
        // Casting at the SQL layer makes the contract explicit and
        // matches the medications/problems repos.
        $rows = $this->connection->fetchAllAssociative(
            "SELECT id, title, reaction, severity_al,
                    DATE(begdate) AS begdate,
                    DATE(enddate) AS enddate
             FROM lists
             WHERE pid = :pid AND type = 'allergy' AND activity = 1
             ORDER BY begdate DESC, id DESC",
            ['pid' => $pid]
        );

        $result = [];
        foreach ($rows as $row) {
            $id = $row['id'] ?? null;
            $title = $row['title'] ?? null;
            $reaction = $row['reaction'] ?? null;
            $severity = $row['severity_al'] ?? null;
            $beg = $row['begdate'] ?? null;
            $end = $row['enddate'] ?? null;
            $result[] = [
                'id' => is_int($id) ? $id : (is_string($id) ? (int) $id : 0),
                'name' => is_string($title) ? $title : '',
                'reaction' => is_string($reaction) && $reaction !== '' ? $reaction : null,
                'severity' => is_string($severity) && $severity !== '' ? $severity : null,
                'begin_date' => is_string($beg) && $beg !== '' ? $beg : null,
                'end_date' => is_string($end) && $end !== '' ? $end : null,
            ];
        }
        return $result;
    }
}
