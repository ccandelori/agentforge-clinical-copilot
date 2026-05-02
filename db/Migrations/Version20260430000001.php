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
 * Composite indexes supporting the AgentForge Clinical Co-Pilot's
 * patient-scoped data retrieval queries (AUDIT.md P2).
 *
 * Each index supports the agent's `WHERE <patient_col> = ? ORDER BY <date_col> DESC LIMIT N`
 * access pattern for one of: lab orders, lab reports, vitals trends, patient
 * notes, and clinical notes.
 *
 * Note: idx_procedure_report_date (added below) leads with the table's
 * PRIMARY KEY and is redundant with the InnoDB clustered index. This was
 * verified in Taskmaster Task 49 and the index is dropped in
 * Version20260502193710. The CREATE statement is preserved here so the
 * migration history still reproduces the schema state at the time it ran;
 * a fresh-install path skips the index entirely via sql/database.sql.
 */
final class Version20260430000001 extends AbstractMigration
{
    public function getDescription(): string
    {
        return 'Add composite indexes for AgentForge agent queries (AUDIT.md P2)';
    }

    public function up(Schema $schema): void
    {
        // Lab order retrieval: agent fetches a patient's recent orders.
        // Existing `datepid (date_ordered, patient_id)` has the wrong column
        // order for this access pattern; this index leads with patient_id.
        $this->addSql(
            'ALTER TABLE `procedure_order` '
            . 'ADD INDEX `idx_procedure_order_patient_date` (`patient_id`, `date_ordered`)'
        );

        // Lab report retrieval. NOTE: this index is redundant with the
        // table's PRIMARY KEY and gets dropped in Version20260502193710.
        // It is preserved here so that re-running migrations from scratch
        // reproduces the historical schema state.
        $this->addSql(
            'ALTER TABLE `procedure_report` '
            . 'ADD INDEX `idx_procedure_report_date` (`procedure_report_id`, `date_report`)'
        );

        // Vitals trend queries.
        $this->addSql(
            'ALTER TABLE `form_vitals` '
            . 'ADD INDEX `idx_form_vitals_pid_date` (`pid`, `date`)'
        );

        // Patient notes by patient + date.
        $this->addSql(
            'ALTER TABLE `pnotes` '
            . 'ADD INDEX `idx_pnotes_pid_date` (`pid`, `date`)'
        );

        // Clinical notes by patient + date.
        $this->addSql(
            'ALTER TABLE `form_clinical_notes` '
            . 'ADD INDEX `idx_clinical_notes_pid_date` (`pid`, `date`)'
        );
    }

    public function down(Schema $schema): void
    {
        // Reverse order of up() so the rollback is idempotent across tables.
        $this->addSql('ALTER TABLE `form_clinical_notes` DROP INDEX `idx_clinical_notes_pid_date`');
        $this->addSql('ALTER TABLE `pnotes` DROP INDEX `idx_pnotes_pid_date`');
        $this->addSql('ALTER TABLE `form_vitals` DROP INDEX `idx_form_vitals_pid_date`');
        $this->addSql('ALTER TABLE `procedure_report` DROP INDEX `idx_procedure_report_date`');
        $this->addSql('ALTER TABLE `procedure_order` DROP INDEX `idx_procedure_order_patient_date`');
    }
}
