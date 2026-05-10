<?php

/**
 * PromotedItemHandle — receipt for one row written to the ``lists``
 * table by the intake-promotion flow.
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
 * Returned by :class:`IntakePromotionWriter` so the controller can
 * surface per-row identifiers back to the dashboard. The dashboard
 * uses these to (a) audit which AI-extracted rows landed in the
 * chart, and (b) potentially offer an "undo" affordance later.
 *
 * ``listsId`` is the auto-increment ``lists.id`` PK assigned by
 * MySQL — it's the simplest stable handle the existing
 * ``AllergiesRepository`` / ``ProblemsRepository`` joins return.
 */
final readonly class PromotedItemHandle
{
    public function __construct(
        public string $kind,
        public int $listsId,
        public string $title,
    ) {
    }
}
