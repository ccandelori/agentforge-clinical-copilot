<?php

/**
 * Value object for the canonical AgentForge intake-form Questionnaire.
 *
 * Carries three identity fields the persistence flow needs:
 *
 *   - `$id`              — bigint FK target for `questionnaire_response.questionnaire_foreign_id`
 *   - `$questionnaireId` — FHIR string logical id (R4 `Questionnaire.id`)
 *                          stored on `questionnaire_response.questionnaire_id`
 *                          so FHIR clients resolve `Questionnaire/{id}` correctly.
 *   - `$name`            — human display name surfaced in the overlay UI;
 *                          NOT a valid FHIR resource id (contains spaces and
 *                          capitals) and intentionally separate from
 *                          `$questionnaireId` so we don't end up writing the
 *                          display string into the resource-id slot.
 *
 * Plus the frozen JSON snapshot used as the `questionnaire_response.questionnaire`
 * column so a response stays structurally readable even if the canonical
 * Questionnaire is later updated.
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
        public string $questionnaireId,
        public string $questionnaireJson,
    ) {
    }
}
