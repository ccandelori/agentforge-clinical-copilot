<?php

/**
 * UserIdentity — immutable resolution of an OpenEMR user's UUID into
 * the integer user_id + username the legacy AGENTFORGE_JWT contract
 * keys off. Produced by UserIdentityRepository; consumed by the
 * dashboard auth bridge (ADR-0001) to mint internal JWTs from a
 * cookie session.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

final readonly class UserIdentity
{
    public function __construct(
        public int $userId,
        public string $username,
    ) {
    }
}
