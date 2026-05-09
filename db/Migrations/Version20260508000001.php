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
 * Backfill `questionnaire_repository.questionnaire_id` for the AgentForge
 * intake-form canonical row.
 *
 * The original Version20260505000001 seed migration left this column
 * NULL because it predated the P4 finding that
 * `questionnaire_response.questionnaire_id` (the FHIR R4 logical id
 * column FHIR clients use to resolve `Questionnaire/{id}`) needs a
 * stable string value, not the human display name.
 *
 * Already-deployed droplets ran the original migration before this
 * fix, so a re-run of Version20260505000001 won't replay (Doctrine
 * tracks applied versions). This second migration is the production
 * backfill path: it sets `questionnaire_id` on the existing row to
 * the canonical logical id, no-op when the column is already set
 * correctly.
 *
 * Mirrors the constant on `IntakeQuestionnaireLookup` and the
 * `QUESTIONNAIRE_LOGICAL_ID` constant on the seed migration — all
 * three sites must agree on the value.
 */
final class Version20260508000001 extends AbstractMigration
{
    private const SOURCE_URL = 'https://agentforge.openemr.org/Questionnaire/intake-form';
    private const QUESTIONNAIRE_LOGICAL_ID = 'agentforge-intake-form';

    public function getDescription(): string
    {
        return 'Backfill AgentForge intake-form questionnaire_id (P4 fix)';
    }

    public function up(Schema $schema): void
    {
        // Set the logical id only on the canonical row scoped by
        // source_url. The condition `IS NULL OR != logical_id`
        // makes the migration safe to re-run and avoids an
        // unnecessary write when the seed migration was re-run on a
        // fresh DB after the P4 fix landed.
        $this->connection->executeStatement(
            'UPDATE questionnaire_repository '
            . 'SET questionnaire_id = :questionnaire_id '
            . 'WHERE source_url = :url '
            . 'AND (questionnaire_id IS NULL OR questionnaire_id <> :questionnaire_id)',
            [
                'questionnaire_id' => self::QUESTIONNAIRE_LOGICAL_ID,
                'url' => self::SOURCE_URL,
            ]
        );
    }

    public function down(Schema $schema): void
    {
        // Reset to NULL only on the canonical row — leaving any other
        // rows that happen to share the logical id untouched.
        $this->connection->executeStatement(
            'UPDATE questionnaire_repository '
            . 'SET questionnaire_id = NULL '
            . 'WHERE source_url = :url',
            ['url' => self::SOURCE_URL]
        );
    }
}
