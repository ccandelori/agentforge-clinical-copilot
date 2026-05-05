<?php

/**
 * Value object for the canonical AgentForge intake-form Questionnaire
 * — its id (FK target) and its frozen JSON snapshot (used as the
 * `questionnaire_response.questionnaire` column so a response stays
 * structurally readable even if the canonical Questionnaire is later
 * updated).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

final readonly class SeededIntakeQuestionnaire
{
    public function __construct(
        public int $id,
        public string $name,
        public string $questionnaireJson,
    ) {
    }
}
