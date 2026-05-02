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
        // Two clinical-relevance filters baked into the SQL:
        //
        //   1. NOT LIKE '%(situation)%' — drops SNOMED "situation"
        //      concept-class rows. These are pure administrative codes
        //      (e.g. "Medication review due (situation)") that don't
        //      belong on a clinician-facing problem list. "(disorder)"
        //      and most "(finding)" rows survive — the latter cover
        //      legitimate SDOH / safety screens that ARE clinically
        //      relevant (housing, IPV, social isolation).
        //
        //   2. ROW_NUMBER() OVER (PARTITION BY diagnosis ...) — Synthea
        //      generates one lists row per encounter that touched a
        //      condition, so a single chronic problem ("Stress",
        //      "Hypertension") can repeat 6+ times. Real EMR problem
        //      lists show one row per distinct condition; we keep the
        //      most-recent (MAX id, MAX begdate) and drop the rest.
        //
        // DATE(begdate) strips the time component — lists.begdate is
        // DATETIME and the sidecar's pydantic ``date | None`` field
        // rejects datetime strings with non-zero time.
        $rows = $this->connection->fetchAllAssociative(
            "SELECT id, title, diagnosis, DATE(begdate) AS begdate
             FROM (
                 SELECT
                     id, title, diagnosis, begdate,
                     ROW_NUMBER() OVER (
                         PARTITION BY diagnosis
                         ORDER BY begdate DESC, id DESC
                     ) AS rn
                 FROM lists
                 WHERE pid = :pid
                   AND type = 'medical_problem'
                   AND activity = 1
                   AND title NOT LIKE '%(situation)%'
             ) ranked
             WHERE rn = 1
             ORDER BY begdate DESC, id DESC
             LIMIT 100",
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
