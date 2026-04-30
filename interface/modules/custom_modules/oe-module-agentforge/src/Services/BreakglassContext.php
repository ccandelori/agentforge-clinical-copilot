<?php

declare(strict_types=1);

/**
 * BreakglassContext — value object capturing whether the current agent
 * turn is operating under break-the-glass and, if so, the reason text
 * that must accompany the elevated access.
 *
 * The constructor enforces the consistency rule: a true flag requires
 * a non-empty (after trim) reason. This matches ARCHITECTURE.md §2's
 * audit contract — every break-the-glass invocation must have a reason
 * recorded against it. Catching missing reasons at construction time
 * keeps the JWT minter from silently signing tokens with empty audit
 * reasons.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use InvalidArgumentException;

final readonly class BreakglassContext
{
    public function __construct(
        public bool $flag,
        public ?string $reason = null,
    ) {
        if ($this->flag && trim((string) $this->reason) === '') {
            throw new InvalidArgumentException(
                'Break-the-glass flag is set but no reason was provided. '
                . 'Every BTG invocation must record a non-empty reason for the '
                . 'audit trail (ARCHITECTURE.md §2).'
            );
        }
    }
}
