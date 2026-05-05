<?php

/**
 * IntakeQuestionnaireLookup — finds the canonical AgentForge intake-form
 * Questionnaire seeded by the W2 Task 5 migration so the persistence
 * endpoint can FK a new QuestionnaireResponse to it.
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

/**
 * Looks up the seeded canonical Questionnaire row by its source_url.
 * Returns the `questionnaire_repository.id` (used as
 * `questionnaire_response.questionnaire_foreign_id`) or null if the
 * seed is missing.
 *
 * The persistence endpoint MUST treat null as a 500 (fail closed) —
 * silently writing an unanchored QuestionnaireResponse would lose the
 * structural connection to the canonical schema and break the FHIR
 * round-trip in the overlay UI.
 */
readonly class IntakeQuestionnaireLookup
{
    public const CANONICAL_URL = 'https://agentforge.openemr.org/Questionnaire/intake-form';

    public function __construct(
        private Connection $connection,
    ) {
    }

    public function findCanonicalQuestionnaire(): ?SeededIntakeQuestionnaire
    {
        $row = $this->connection->fetchAssociative(
            'SELECT id, name, questionnaire FROM questionnaire_repository '
            . 'WHERE source_url = :url LIMIT 1',
            ['url' => self::CANONICAL_URL],
        );

        if ($row === false) {
            return null;
        }

        $id = isset($row['id']) && is_numeric($row['id']) ? (int) $row['id'] : 0;
        if ($id <= 0) {
            return null;
        }

        $name = isset($row['name']) && is_string($row['name']) ? $row['name'] : 'AgentForge Intake Form';
        $questionnaireJson = isset($row['questionnaire']) && is_string($row['questionnaire'])
            ? $row['questionnaire']
            : '';

        return new SeededIntakeQuestionnaire(
            id: $id,
            name: $name,
            questionnaireJson: $questionnaireJson,
        );
    }
}
