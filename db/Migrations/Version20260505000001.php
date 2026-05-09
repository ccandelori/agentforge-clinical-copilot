<?php

/**
 * @package   openemr
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Core\Migrations;

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

/**
 * Seed the canonical AgentForge intake-form FHIR Questionnaire row.
 *
 * The AgentForge intake-form persistence flow (W2 Task 12) writes a
 * QuestionnaireResponse against this exact Questionnaire by URL, so the
 * row must exist before any intake-form upload can be persisted. The
 * canonical URL is `https://agentforge.openemr.org/Questionnaire/intake-form`.
 *
 * The Questionnaire's `item` set mirrors the Pydantic
 * IntakeFormExtraction shape (W2 Task 4):
 *
 *   chief_concern              (text, single)
 *   demographics               (group, single)
 *     - date_of_birth, sex, address, phone, email
 *   medications                (group, repeats)
 *     - name (required), dose, frequency
 *   allergies                  (group, repeats)
 *     - substance (required), reaction, severity
 *   family_history             (group, repeats)
 *     - relative (required), condition (required)
 *
 * Adding/removing an item here is a coordinated change with the Pydantic
 * model, the worker prompt that produces the structured output, and the
 * persistence endpoint that maps Pydantic → FHIR QuestionnaireResponse.
 *
 * ## Idempotency note
 *
 * `questionnaire_repository.source_url` has NO unique index on this fork
 * (verified `sql/database.sql:14342`), so a DB-enforced upsert via
 * `ON DUPLICATE KEY UPDATE` is unavailable. The migration uses a
 * SELECT-then-INSERT-or-UPDATE pattern to stay idempotent: re-running
 * over an existing row updates that row in place rather than producing
 * a duplicate. This means production can safely run the migration twice
 * (e.g. partial deploy, retry) without polluting the table.
 *
 * The down() likewise uses a parameterized DELETE on `source_url`, which
 * is safe against duplicates that any *other* migration might have left
 * around: it only removes rows owned by this exact canonical URL.
 */
final class Version20260505000001 extends AbstractMigration
{
    private const SOURCE_URL = 'https://agentforge.openemr.org/Questionnaire/intake-form';
    private const QUESTIONNAIRE_NAME = 'AgentForge Intake Form';
    private const QUESTIONNAIRE_TYPE = 'Questionnaire';
    private const QUESTIONNAIRE_STATUS = 'active';

    /**
     * FHIR R4 logical id for this Questionnaire. Stored on
     * `questionnaire_repository.questionnaire_id` and copied onto every
     * resulting `questionnaire_response.questionnaire_id` row so FHIR
     * clients resolve `Questionnaire/{id}` correctly. Mirrors the
     * `IntakeQuestionnaireLookup::QUESTIONNAIRE_ID` constant — both
     * sites must agree (the lookup falls back to its constant when the
     * stored value is NULL on legacy seed rows, which is the upgrade
     * path for droplets that ran an earlier version of this migration).
     */
    private const QUESTIONNAIRE_LOGICAL_ID = 'agentforge-intake-form';

    public function getDescription(): string
    {
        return 'Seed canonical AgentForge intake-form Questionnaire (W2 Task 5)';
    }

    public function up(Schema $schema): void
    {
        $questionnaireJson = $this->buildQuestionnaireJson();

        $existing = $this->connection->fetchOne(
            'SELECT id FROM questionnaire_repository WHERE source_url = :url',
            ['url' => self::SOURCE_URL]
        );

        if ($existing === false) {
            // Row does not exist — INSERT.
            $this->connection->executeStatement(
                'INSERT INTO questionnaire_repository '
                . '(name, type, status, source_url, questionnaire_id, questionnaire) '
                . 'VALUES (:name, :type, :status, :url, :questionnaire_id, :questionnaire)',
                [
                    'name' => self::QUESTIONNAIRE_NAME,
                    'type' => self::QUESTIONNAIRE_TYPE,
                    'status' => self::QUESTIONNAIRE_STATUS,
                    'url' => self::SOURCE_URL,
                    'questionnaire_id' => self::QUESTIONNAIRE_LOGICAL_ID,
                    'questionnaire' => $questionnaireJson,
                ]
            );
            return;
        }

        // Row exists — UPDATE in place. Idempotent re-run path.
        // Re-running this migration on a droplet that originally seeded
        // the row WITHOUT a questionnaire_id will now backfill it,
        // which is the intended upgrade path for the P4 fix.
        $this->connection->executeStatement(
            'UPDATE questionnaire_repository SET '
            . 'name = :name, type = :type, status = :status, '
            . 'questionnaire_id = :questionnaire_id, questionnaire = :questionnaire '
            . 'WHERE source_url = :url',
            [
                'name' => self::QUESTIONNAIRE_NAME,
                'type' => self::QUESTIONNAIRE_TYPE,
                'status' => self::QUESTIONNAIRE_STATUS,
                'questionnaire_id' => self::QUESTIONNAIRE_LOGICAL_ID,
                'questionnaire' => $questionnaireJson,
                'url' => self::SOURCE_URL,
            ]
        );
    }

    public function down(Schema $schema): void
    {
        $this->connection->executeStatement(
            'DELETE FROM questionnaire_repository WHERE source_url = :url',
            ['url' => self::SOURCE_URL]
        );
    }

    /**
     * Build the FHIR R4 Questionnaire payload as a JSON string suitable
     * for the `questionnaire_repository.questionnaire` longtext column.
     *
     * The structure mirrors the Pydantic IntakeFormExtraction model
     * one-to-one. linkIds match the model field names so the Task 12
     * persistence endpoint can map structured extraction → FHIR
     * QuestionnaireResponse without a translation table.
     *
     * @throws \JsonException when encoding fails — should never happen
     * for the literal in-memory structure below, but we let it propagate
     * rather than swallow it (CLAUDE.md: do not catch-log-continue).
     */
    private function buildQuestionnaireJson(): string
    {
        $questionnaire = [
            'resourceType' => 'Questionnaire',
            'id' => self::QUESTIONNAIRE_LOGICAL_ID,
            'url' => self::SOURCE_URL,
            'name' => 'AgentForgeIntakeForm',
            'title' => self::QUESTIONNAIRE_NAME,
            'status' => self::QUESTIONNAIRE_STATUS,
            'subjectType' => ['Patient'],
            'item' => [
                [
                    'linkId' => 'chief_concern',
                    'text' => 'Chief Concern',
                    'type' => 'text',
                    'required' => false,
                ],
                [
                    'linkId' => 'demographics',
                    'text' => 'Demographics',
                    'type' => 'group',
                    'item' => [
                        [
                            'linkId' => 'date_of_birth',
                            'text' => 'Date of Birth',
                            'type' => 'date',
                        ],
                        [
                            'linkId' => 'sex',
                            'text' => 'Sex',
                            'type' => 'string',
                        ],
                        [
                            'linkId' => 'address',
                            'text' => 'Address',
                            'type' => 'string',
                        ],
                        [
                            'linkId' => 'phone',
                            'text' => 'Phone',
                            'type' => 'string',
                        ],
                        [
                            'linkId' => 'email',
                            'text' => 'Email',
                            'type' => 'string',
                        ],
                    ],
                ],
                [
                    'linkId' => 'medications',
                    'text' => 'Medications',
                    'type' => 'group',
                    'repeats' => true,
                    'item' => [
                        [
                            'linkId' => 'name',
                            'text' => 'Medication Name',
                            'type' => 'string',
                            'required' => true,
                        ],
                        [
                            'linkId' => 'dose',
                            'text' => 'Dose',
                            'type' => 'string',
                        ],
                        [
                            'linkId' => 'frequency',
                            'text' => 'Frequency',
                            'type' => 'string',
                        ],
                    ],
                ],
                [
                    'linkId' => 'allergies',
                    'text' => 'Allergies',
                    'type' => 'group',
                    'repeats' => true,
                    'item' => [
                        [
                            'linkId' => 'substance',
                            'text' => 'Substance',
                            'type' => 'string',
                            'required' => true,
                        ],
                        [
                            'linkId' => 'reaction',
                            'text' => 'Reaction',
                            'type' => 'string',
                        ],
                        [
                            'linkId' => 'severity',
                            'text' => 'Severity',
                            'type' => 'string',
                        ],
                    ],
                ],
                [
                    'linkId' => 'family_history',
                    'text' => 'Family History',
                    'type' => 'group',
                    'repeats' => true,
                    'item' => [
                        [
                            'linkId' => 'relative',
                            'text' => 'Relative',
                            'type' => 'string',
                            'required' => true,
                        ],
                        [
                            'linkId' => 'condition',
                            'text' => 'Condition',
                            'type' => 'string',
                            'required' => true,
                        ],
                    ],
                ],
            ],
        ];

        return json_encode($questionnaire, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    }
}
