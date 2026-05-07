<?php

/**
 * PatientPidRepository — resolves a FHIR Patient resource UUID into
 * the integer ``patient_data.pid`` the agent's JWT contract carries.
 *
 * Pairs with :class:`UserIdentityRepository`: the dashboard auth
 * bridge (ADR-0001) needs both a user identity (UUID → user_id) and
 * a patient identity (UUID → pid) before it can mint an internal
 * JWT. Keeping them separate keeps each lookup focused on a single
 * SQL contract.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class PatientPidRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * Look up a patient's integer pid by their FHIR resource UUID.
     * Returns null when the UUID has no matching row, or when the
     * row's pid column is non-numeric (degraded data → caller
     * responds 404).
     */
    public function findPidByUuid(string $uuid): ?int
    {
        $sql = 'SELECT pid FROM patient_data WHERE uuid = ? LIMIT 1';

        $row = $this->connection->fetchOne($sql, [$uuid]);
        if ($row === false) {
            return null;
        }
        if (is_int($row)) {
            return $row;
        }
        if (is_string($row) && ctype_digit($row)) {
            return (int) $row;
        }
        return null;
    }
}
