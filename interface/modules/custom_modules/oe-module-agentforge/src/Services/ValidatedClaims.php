<?php

declare(strict_types=1);

/**
 * Immutable claims DTO returned by AgentJwtValidator on successful
 * verification of a sidecar→PHP internal endpoint request.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

final readonly class ValidatedClaims
{
    public function __construct(
        public int $userId,
        public int $patientId,
    ) {
    }
}
