<?php

/**
 * PromotionResult — aggregate handle for a successful intake-promotion
 * write batch.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

/**
 * Carries one :class:`PromotedItemHandle` per row inserted by the
 * :class:`IntakePromotionWriter` cascade. The controller serialises
 * this into the JSON response body so the dashboard can render a
 * per-row receipt and trigger card refreshes.
 *
 * The list is non-empty by construction in the controller (it
 * rejects empty batches up front) but we keep the type
 * ``list<PromotedItemHandle>`` rather than ``non-empty-list`` here
 * to keep the constructor reusable from tests that exercise the
 * empty-result edge case via direct construction.
 */
final readonly class PromotionResult
{
    /**
     * @param list<PromotedItemHandle> $handles
     */
    public function __construct(
        public array $handles,
    ) {
    }
}
