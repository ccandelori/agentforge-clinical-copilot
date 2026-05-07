<?php

/**
 * UserIdentityRepository — resolves an OpenEMR user UUID to the
 * integer user_id + username that the legacy AGENTFORGE_JWT contract
 * keys off.
 *
 * The dashboard auth bridge (ADR-0001) needs this resolution because
 * the OIDC session identity it inherits is a UUID URI
 * (`Practitioner/<uuid>`), but the agent's RequestContext —
 * minted into the internal JWT — is identified by the integer
 * `users.id`. The mapping lives in `users.uuid → users.id`.
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

class UserIdentityRepository
{
    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * Look up a user's integer id + username by their UUID. Returns
     * null when the UUID has no matching row, or when the row is
     * missing required columns (degraded data → caller responds 404
     * rather than synthesise partial identity).
     */
    public function findByUuid(string $uuid): ?UserIdentity
    {
        $sql = 'SELECT id, username FROM users WHERE uuid = ? LIMIT 1';

        $row = $this->connection->fetchAssociative($sql, [$uuid]);
        if ($row === false) {
            return null;
        }

        $id = $row['id'] ?? null;
        $username = $row['username'] ?? null;
        if (!is_int($id) && !(is_string($id) && ctype_digit($id))) {
            return null;
        }
        if (!is_string($username) || $username === '') {
            return null;
        }

        return new UserIdentity(
            userId: (int) $id,
            username: $username,
        );
    }
}
