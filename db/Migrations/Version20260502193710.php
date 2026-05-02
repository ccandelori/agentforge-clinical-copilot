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
 * Drop redundant `idx_procedure_report_date` (Taskmaster Task 49).
 *
 * Version20260430000001 added `idx_procedure_report_date(procedure_report_id,
 * date_report)` to `procedure_report`, but the leading column is the table's
 * PRIMARY KEY. InnoDB tables are clustered on the primary key, so any
 * secondary index that leads with the full PK is strictly dominated by the
 * clustered index for every meaningful access pattern:
 *
 * - PK equality lookups go to PRIMARY (verified via EXPLAIN).
 * - Joins on `procedure_order_id` use the dedicated `procedure_order_id`
 *   secondary index.
 * - The agent's lab query (src/Services/ObservationLabService.php) joins
 *   procedure_result -> procedure_report -> procedure_order, scoped by
 *   procedure_order.patient_id. EXPLAIN shows the optimizer never selects
 *   `idx_procedure_report_date` for that plan; it lists the index in
 *   possible_keys but picks NULL (the small-table scan), PRIMARY, or
 *   `procedure_order_id` instead.
 * - The covering-index angle does not apply either: agent queries also need
 *   `procedure_order_id` (not in the secondary index), so MariaDB still has
 *   to dereference the clustered row.
 *
 * The original index was added defensively per the AUDIT.md P2 spec. Task 49
 * is the post-hoc verification step the spec called out, and the conclusion
 * is "drop it". Keeping it would cost one B-tree's worth of writes on every
 * INSERT/UPDATE to procedure_report for zero read-side benefit.
 */
final class Version20260502193710 extends AbstractMigration
{
    public function getDescription(): string
    {
        return 'Drop redundant idx_procedure_report_date — leads with PK (Task 49)';
    }

    public function up(Schema $schema): void
    {
        $this->addSql('ALTER TABLE `procedure_report` DROP INDEX `idx_procedure_report_date`');
    }

    public function down(Schema $schema): void
    {
        // Symmetric rollback: re-add the index identically to Version20260430000001.
        $this->addSql(
            'ALTER TABLE `procedure_report` '
            . 'ADD INDEX `idx_procedure_report_date` (`procedure_report_id`, `date_report`)'
        );
    }
}
