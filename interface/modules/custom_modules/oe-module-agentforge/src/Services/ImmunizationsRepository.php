<?php

declare(strict_types=1);

/**
 * ImmunizationsRepository — typed read of the immunizations table for the
 * agent's get_immunizations tool.
 *
 * MVP scope: returns the patient's full immunization history with each
 * row's CVX code, administered date, and (when resolvable) a human-
 * readable vaccine name from the ``codes`` table at code_type=100.
 * Erroneously-added rows are excluded — they're soft-deletes the user
 * already retracted, and surfacing them would mislead the agent.
 *
 * Reads via Doctrine DBAL so the connection lifecycle matches the rest
 * of the internal endpoints. See ARCHITECTURE.md §4 (tool layer).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class ImmunizationsRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return array<int, array{
     *     id: int,
     *     cvx_code: string|null,
     *     vaccine_name: string|null,
     *     administered_date: string|null,
     *     manufacturer: string|null,
     *     lot_number: string|null,
     *     note: string|null
     * }>
     */
    public function findByPid(int $pid): array
    {
        // Vaccine-name resolution: CVX codes (HL7 vaccine code set) live
        // in the ``codes`` table at code_type=100. There can be several
        // rows per CVX (different brand variants share a code), so we
        // pick one canonical text via a correlated subquery with
        // LIMIT 1. NULL when the CVX isn't seeded — the agent still
        // gets the raw cvx_code in that case.
        //
        // DATE() strips the time component from administered_date
        // (a DATETIME column). Synthea-imported rows ship with
        // 00:00:00, but the contract holds for any future source.
        //
        // ``added_erroneously`` is the soft-delete flag — rows with
        // value 1 are mistakes the user retracted. Filtered out at the
        // SQL layer so the agent never sees them.
        $rows = $this->connection->fetchAllAssociative(
            "SELECT
                 i.id,
                 i.cvx_code,
                 (SELECT c.code_text
                  FROM codes c
                  WHERE c.code = i.cvx_code
                    AND c.code_type = 100
                  LIMIT 1) AS vaccine_name,
                 DATE(i.administered_date) AS administered_date,
                 i.manufacturer,
                 i.lot_number,
                 i.note
             FROM immunizations i
             WHERE i.patient_id = :pid
               AND (i.added_erroneously IS NULL OR i.added_erroneously = 0)
             ORDER BY i.administered_date DESC, i.id DESC
             LIMIT 100",
            ['pid' => $pid]
        );

        $result = [];
        foreach ($rows as $row) {
            $id = $row['id'] ?? null;
            $cvx = $row['cvx_code'] ?? null;
            $name = $row['vaccine_name'] ?? null;
            $admin = $row['administered_date'] ?? null;
            $manufacturer = $row['manufacturer'] ?? null;
            $lot = $row['lot_number'] ?? null;
            $note = $row['note'] ?? null;
            $result[] = [
                'id' => is_int($id) ? $id : (is_string($id) ? (int) $id : 0),
                'cvx_code' => is_string($cvx) && $cvx !== '' ? $cvx : null,
                'vaccine_name' => is_string($name) && $name !== '' ? $name : null,
                'administered_date' => is_string($admin) && $admin !== '' ? $admin : null,
                'manufacturer' => is_string($manufacturer) && $manufacturer !== '' ? $manufacturer : null,
                'lot_number' => is_string($lot) && $lot !== '' ? $lot : null,
                'note' => is_string($note) && $note !== '' ? $note : null,
            ];
        }
        return $result;
    }
}
