<?php

declare(strict_types=1);

/**
 * UserRoleLookup — resolves an OpenEMR user's primary GACL group name.
 *
 * The Python sidecar's auth gateway needs a coarse role string per JWT
 * to make sensitivity-policy decisions (ARCHITECTURE.md §2). OpenEMR's
 * GACL schema stores group memberships across `gacl_aro` (users),
 * `gacl_groups_aro_map` (membership), and `gacl_aro_groups` (groups);
 * we surface a single primary group via the lowest-id deterministic
 * tiebreaker so the JWT carries a stable role across requests for the
 * same user.
 *
 * The query shape mirrors `OpenEMR\Common\Logging\BreakglassChecker` —
 * same pattern, just selecting the group name instead of checking
 * membership in the breakglass group.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

class UserRoleLookup
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * Return the user's primary GACL group name, or null if the user has
     * no GACL group memberships (which usually means an unauthenticated
     * or freshly-created account).
     */
    public function findPrimaryGroup(string $username): ?string
    {
        $sql = <<<'SQL'
            SELECT grp.value
            FROM gacl_aro aro
            JOIN gacl_groups_aro_map map ON aro.id = map.aro_id
            JOIN gacl_aro_groups grp ON map.group_id = grp.id
            WHERE BINARY aro.value = ?
            ORDER BY grp.id ASC
            LIMIT 1
            SQL;

        $result = $this->connection->fetchOne($sql, [$username]);

        // fetchOne returns mixed; narrow to string-or-null per CLAUDE.md.
        // gacl_aro_groups.value is VARCHAR so a hit is always string,
        // and a miss is the literal `false`.
        return is_string($result) ? $result : null;
    }
}
