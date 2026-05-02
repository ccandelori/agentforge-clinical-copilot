<?php

/**
 * EncountersRepository — typed read of form_encounter for the agent's
 * get_recent_encounters tool.
 *
 * MVP scope: returns the patient's recent encounters joined to `users` so
 * the agent can see the provider username alongside each visit. We mirror
 * the custom-internal-endpoint pattern used by every other AgentForge
 * tool (NotesRepository, LabsRepository, etc.) rather than using the
 * built-in FHIR Encounter route, which would require OAuth2 credentials
 * the sidecar doesn't have. The Python sidecar adapts the JSON shape.
 *
 * Filter rules:
 *   - pid scope (the controller already enforces a JWT-bound pid match —
 *     this is defense-in-depth at the data layer).
 *   - lookback window of `days` (clamped to [1, 730]; encounters span
 *     longer windows than labs/notes so a year+ default is reasonable).
 *
 * Schema gotchas:
 *   - form_encounter.provider_id defaults to 0 ("no provider" sentinel
 *     from legacy schema) and may also be null after the LEFT JOIN finds
 *     no match. Both cases normalize to null on the way out.
 *   - form_encounter.class_code is NOT NULL with a default of "AMB"; we
 *     still null-out empty strings defensively.
 *   - pc_catid is the postcalendar category id (NOT NULL, default 5).
 *     0 is theoretically a valid id, so we keep it as an int rather than
 *     coerce to null.
 *
 * The query relies on the existing `encounter_date` index on (date) and
 * `pid_encounter` index on (pid, encounter); no new schema is required.
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

class EncountersRepository
{
    private const ROW_LIMIT = 50;
    private const MIN_DAYS = 1;
    private const MAX_DAYS = 730;

    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return list<array{
     *     id: int,
     *     date: string|null,
     *     reason: string|null,
     *     encounter_type: string|null,
     *     class_code: string|null,
     *     provider_id: int|null,
     *     provider_name: string|null,
     *     sensitivity: string|null,
     *     encounter_category: int|null
     * }>
     */
    public function findRecentByPid(int $pid, int $days = 365): array
    {
        $clampedDays = max(self::MIN_DAYS, min(self::MAX_DAYS, $days));
        $since = (new DateTimeImmutable())
            ->modify("-{$clampedDays} days")
            ->format('Y-m-d H:i:s');

        // LEFT JOIN so encounters with provider_id = 0 (legacy "no provider"
        // sentinel) still surface — the JOIN simply returns null for the
        // username, which we then normalize alongside the 0 -> null rule.
        $sql = "
            SELECT e.id, e.`date`, e.reason,
                   e.encounter_type_description AS encounter_type,
                   e.class_code, e.provider_id, u.username AS provider_name,
                   e.sensitivity, e.pc_catid AS encounter_category
            FROM form_encounter e
            LEFT JOIN users u ON u.id = e.provider_id
            WHERE e.pid = :pid AND e.`date` >= :since
            ORDER BY e.`date` DESC
            LIMIT " . self::ROW_LIMIT;

        $rows = $this->connection->fetchAllAssociative(
            $sql,
            ['pid' => $pid, 'since' => $since]
        );

        $result = [];
        foreach ($rows as $row) {
            $idRaw = $row['id'] ?? null;
            $dateRaw = $row['date'] ?? null;
            $reasonRaw = $row['reason'] ?? null;
            $encounterTypeRaw = $row['encounter_type'] ?? null;
            $classCodeRaw = $row['class_code'] ?? null;
            $providerIdRaw = $row['provider_id'] ?? null;
            $providerNameRaw = $row['provider_name'] ?? null;
            $sensitivityRaw = $row['sensitivity'] ?? null;
            $encounterCategoryRaw = $row['encounter_category'] ?? null;

            $providerId = is_int($providerIdRaw)
                ? $providerIdRaw
                : (is_string($providerIdRaw) ? (int) $providerIdRaw : 0);
            // Normalize the "no provider" sentinel (0) and the LEFT JOIN
            // miss (null) into the same null signal for the agent.
            $providerIdNormalized = $providerId === 0 ? null : $providerId;

            $result[] = [
                'id' => is_int($idRaw) ? $idRaw : (is_string($idRaw) ? (int) $idRaw : 0),
                'date' => is_string($dateRaw) && $dateRaw !== '' ? $dateRaw : null,
                'reason' => is_string($reasonRaw) && $reasonRaw !== ''
                    ? $reasonRaw : null,
                'encounter_type' => is_string($encounterTypeRaw)
                    && $encounterTypeRaw !== '' ? $encounterTypeRaw : null,
                'class_code' => is_string($classCodeRaw) && $classCodeRaw !== ''
                    ? $classCodeRaw : null,
                'provider_id' => $providerIdNormalized,
                'provider_name' => is_string($providerNameRaw)
                    && $providerNameRaw !== '' ? $providerNameRaw : null,
                'sensitivity' => is_string($sensitivityRaw)
                    && $sensitivityRaw !== '' ? $sensitivityRaw : null,
                'encounter_category' => is_int($encounterCategoryRaw)
                    ? $encounterCategoryRaw
                    : (is_string($encounterCategoryRaw)
                        ? (int) $encounterCategoryRaw : null),
            ];
        }
        return $result;
    }
}
