<?php

/**
 * PromotionItem — value object for one accepted intake-form row that
 * the clinician approved for promotion into the structured chart.
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
 * One accepted item ready for insertion into the ``lists`` table.
 *
 * The controller parses the request body into a ``list<PromotionItem>``
 * — that's the single point at which raw JSON becomes typed data, so
 * downstream callers (writer, audit) can trust the field shapes
 * without re-validating.
 *
 * - ``kind`` is the ``lists.type`` enum value (allergy / medical_problem
 *   / medication / family_history). Drawn from
 *   :class:`IntakePromotionWriter`'s class constants so adding a new
 *   list type is a coordinated change.
 * - ``title`` lands in ``lists.title`` — for allergies it's the
 *   substance, for problems it's the condition, for medications it's
 *   the drug name, for family history it's "relative: condition".
 * - ``details`` is appended to ``lists.comments`` when non-null. It
 *   carries the optional secondary fields the intake form captured
 *   (allergy reaction/severity, medication dose/frequency).
 */
final readonly class PromotionItem
{
    public function __construct(
        public string $kind,
        public string $title,
        public ?string $details = null,
    ) {
    }
}
