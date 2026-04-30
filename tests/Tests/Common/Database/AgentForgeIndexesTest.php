<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Common\Database;

use OpenEMR\Common\Database\QueryUtils;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Verifies the composite and FULLTEXT indexes that AgentForge agent
 * queries depend on (AUDIT.md P2). The indexes are introduced by
 * Doctrine migrations Version20260430000001 and Version20260430000002,
 * and are also baked into sql/database.sql so fresh installs include
 * them. This test passes if either path applied them — its job is to
 * be the pre-deploy regression gate, not to care which path got us here.
 *
 * EXPLAIN-based query plan checks would require populated test data
 * (empty tables make the optimizer choose "Impossible WHERE" or other
 * non-representative plans), so we verify presence-and-shape via
 * information_schema instead. Plan-level verification belongs to a
 * future task once representative agent queries land with fixtures.
 */
final class AgentForgeIndexesTest extends TestCase
{
    /**
     * @return array<string, array{string, string, string, string}>
     *
     * @codeCoverageIgnore Data providers run before coverage instrumentation starts.
     */
    public static function indexProvider(): array
    {
        return [
            'procedure_order patient+date' => [
                'procedure_order',
                'idx_procedure_order_patient_date',
                'patient_id,date_ordered',
                'BTREE',
            ],
            'procedure_report id+date (see Task 49)' => [
                'procedure_report',
                'idx_procedure_report_date',
                'procedure_report_id,date_report',
                'BTREE',
            ],
            'form_vitals pid+date' => [
                'form_vitals',
                'idx_form_vitals_pid_date',
                'pid,date',
                'BTREE',
            ],
            'pnotes pid+date' => [
                'pnotes',
                'idx_pnotes_pid_date',
                'pid,date',
                'BTREE',
            ],
            'pnotes body fulltext' => [
                'pnotes',
                'ft_pnotes_body',
                'body',
                'FULLTEXT',
            ],
            'form_clinical_notes pid+date' => [
                'form_clinical_notes',
                'idx_clinical_notes_pid_date',
                'pid,date',
                'BTREE',
            ],
            'form_clinical_notes description fulltext' => [
                'form_clinical_notes',
                'ft_clinical_notes_desc',
                'description',
                'FULLTEXT',
            ],
        ];
    }

    #[Test]
    #[DataProvider('indexProvider')]
    public function agentIndexExistsWithExpectedShape(
        string $table,
        string $indexName,
        string $expectedColumns,
        string $expectedType,
    ): void {
        $rows = QueryUtils::fetchRecordsNoLog(
            'SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS COLS, INDEX_TYPE
             FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME = ?
               AND INDEX_NAME = ?
             GROUP BY INDEX_NAME, INDEX_TYPE',
            [$table, $indexName]
        );

        $this->assertCount(
            1,
            $rows,
            sprintf('Expected index %s.%s does not exist', $table, $indexName)
        );
        $this->assertSame(
            $expectedColumns,
            $rows[0]['COLS'],
            sprintf('Index %s.%s has unexpected columns', $table, $indexName)
        );
        $this->assertSame(
            $expectedType,
            $rows[0]['INDEX_TYPE'],
            sprintf('Index %s.%s has unexpected type', $table, $indexName)
        );
    }
}
