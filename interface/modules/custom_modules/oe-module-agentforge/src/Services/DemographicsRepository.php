<?php

declare(strict_types=1);

/**
 * DemographicsRepository — typed read of patient_data for the agent's
 * get_demographics tool.
 *
 * MVP scope: returns the small set of fields a clinical co-pilot needs
 * to summarize a patient (name, DOB, sex, preferred language). Reads
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
use RuntimeException;

class DemographicsRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return array{patient_id: int, given_name: string, family_name: string, date_of_birth: string|null, sex: string|null, preferred_language: string|null}
     */
    public function findByPid(int $pid): array
    {
        $row = $this->connection->fetchAssociative(
            'SELECT pid, fname, lname, DOB, sex, language FROM patient_data WHERE pid = :pid',
            ['pid' => $pid]
        );
        if ($row === false) {
            throw new RuntimeException('Patient not found');
        }

        $dob = $row['DOB'] ?? null;
        $pid = $row['pid'] ?? null;
        return [
            'patient_id' => is_int($pid) ? $pid : (is_string($pid) ? (int) $pid : 0),
            'given_name' => is_string($row['fname'] ?? null) ? $row['fname'] : '',
            'family_name' => is_string($row['lname'] ?? null) ? $row['lname'] : '',
            'date_of_birth' => is_string($dob) && $dob !== '' ? $dob : null,
            'sex' => is_string($row['sex'] ?? null) && $row['sex'] !== '' ? $row['sex'] : null,
            'preferred_language' => is_string($row['language'] ?? null) && $row['language'] !== ''
                ? $row['language']
                : null,
        ];
    }
}
