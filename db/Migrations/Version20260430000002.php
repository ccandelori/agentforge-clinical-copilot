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
 * FULLTEXT indexes powering the AgentForge agent's `search_notes` tool.
 *
 * The agent's note-search query (ARCHITECTURE.md §4.1.1) is a per-patient
 * MATCH...AGAINST against pnotes.body and form_clinical_notes.description,
 * scoped first by `pid` (handled by the composite indexes added in
 * Version20260430000001). FULLTEXT indexes make the natural-language
 * scoring portion of the query feasible at the per-patient subset.
 *
 * Kept as a separate migration from the regular composite indexes per
 * db/README.md guidance ("aim to limit any given migration to a single
 * table if possible"); FULLTEXT is also a different physical structure
 * with different rebuild characteristics, so isolating it makes failure
 * analysis cleaner.
 */
final class Version20260430000002 extends AbstractMigration
{
    public function getDescription(): string
    {
        return 'Add FULLTEXT indexes for AgentForge note search (ARCHITECTURE.md §4.1.1)';
    }

    public function up(Schema $schema): void
    {
        $this->addSql(
            'ALTER TABLE `pnotes` ADD FULLTEXT INDEX `ft_pnotes_body` (`body`)'
        );
        $this->addSql(
            'ALTER TABLE `form_clinical_notes` '
            . 'ADD FULLTEXT INDEX `ft_clinical_notes_desc` (`description`)'
        );
    }

    public function down(Schema $schema): void
    {
        $this->addSql('ALTER TABLE `form_clinical_notes` DROP INDEX `ft_clinical_notes_desc`');
        $this->addSql('ALTER TABLE `pnotes` DROP INDEX `ft_pnotes_body`');
    }
}
