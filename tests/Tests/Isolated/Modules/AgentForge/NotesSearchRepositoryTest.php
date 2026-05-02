<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\AgentForge;

use Doctrine\DBAL\Connection;
use OpenEMR\Modules\AgentForge\Services\NotesSearchRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for NotesSearchRepository.
 *
 * The repository runs a patient-scoped FULLTEXT search across pnotes (body)
 * and form_clinical_notes (description) using NATURAL LANGUAGE MODE, then
 * UNIONs the two ranked result sets so the agent's search_notes tool sees
 * a single relevance-ordered list. Tests pin both the SQL shape (filters,
 * MATCH/AGAINST, ordering, lookback window, limit clamping) and the
 * per-source value mapping the agent will see.
 */
final class NotesSearchRepositoryTest extends TestCase
{
    #[Test]
    public function searchQueriesBothTablesWithNaturalLanguageMatch(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 30);

        self::assertStringContainsString('FROM pnotes', $captured['sql']);
        self::assertStringContainsString('FROM form_clinical_notes', $captured['sql']);
        self::assertStringContainsString('UNION ALL', $captured['sql']);
        self::assertStringContainsString('MATCH(body) AGAINST', $captured['sql']);
        self::assertStringContainsString('MATCH(description) AGAINST', $captured['sql']);
        // Both branches of the UNION should use NATURAL LANGUAGE MODE — there
        // are two MATCH/AGAINST sites (one per table), and both need it.
        self::assertSame(
            4,
            substr_count($captured['sql'], 'IN NATURAL LANGUAGE MODE'),
            'Expected MATCH ... AGAINST IN NATURAL LANGUAGE MODE in '
            . 'both SELECT projections AND both WHERE filters.',
        );
    }

    #[Test]
    public function searchScopesBothTablesByPidAndFiltersDeletedAndInactive(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 30);

        // pnotes uses `deleted = 0`, form_clinical_notes uses `activity = 1`.
        // Without these the agent could surface tombstoned rows.
        self::assertStringContainsString('pid = :pid', $captured['sql']);
        self::assertStringContainsString('deleted = 0', $captured['sql']);
        self::assertStringContainsString('activity = 1', $captured['sql']);
        self::assertSame(
            2,
            substr_count($captured['sql'], 'WHERE'),
            'Expected one WHERE per SELECT in the UNION.',
        );
    }

    #[Test]
    public function searchOrdersByScoreDescAndLimitsResults(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 30);

        self::assertStringContainsString('ORDER BY score DESC', $captured['sql']);
        self::assertStringContainsString('LIMIT 5', $captured['sql']);
    }

    #[Test]
    public function searchBindsPidQueryAndSinceParameters(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 30);

        $params = $captured['params'];
        self::assertSame(123, $params['pid']);
        self::assertSame('cough', $params['q']);
        self::assertIsString($params['since']);
        self::assertMatchesRegularExpression(
            '/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/',
            $params['since'],
        );
    }

    #[Test]
    public function searchTrimsWhitespaceFromQueryBeforeBinding(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, "  cough  \n", 5, 30);

        self::assertSame('cough', $captured['params']['q']);
    }

    #[Test]
    public function searchReturnsEmptyArrayWithoutHittingDbForEmptyQuery(): void
    {
        // Defense in depth: even if the controller forgets to reject empty
        // queries, the repository must not call MATCH AGAINST with '' — it
        // wastes a round trip and can mis-rank.
        $connection = self::createMock(Connection::class);
        $connection->expects(self::never())->method('fetchAllAssociative');
        $repository = new NotesSearchRepository($connection);

        $rows = $repository->search(123, '');

        self::assertSame([], $rows);
    }

    #[Test]
    public function searchReturnsEmptyArrayWithoutHittingDbForWhitespaceQuery(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->expects(self::never())->method('fetchAllAssociative');
        $repository = new NotesSearchRepository($connection);

        $rows = $repository->search(123, "   \t\n  ");

        self::assertSame([], $rows);
    }

    #[Test]
    public function searchClampsLimitBelowOne(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 0, 30);

        self::assertStringContainsString('LIMIT 1', $captured['sql']);
    }

    #[Test]
    public function searchClampsLimitAboveTen(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 999, 30);

        self::assertStringContainsString('LIMIT 10', $captured['sql']);
    }

    #[Test]
    public function searchClampsExcessiveDaysToMaximum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 99999);

        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        $maxLookbackSeconds = 365 * 86400;
        self::assertGreaterThanOrEqual(
            time() - $maxLookbackSeconds - 60,
            $sinceTimestamp,
        );
    }

    #[Test]
    public function searchClampsZeroOrNegativeDaysToMinimum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesSearchRepository($connection);

        $repository->search(123, 'cough', 5, 0);

        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        self::assertLessThan(time(), $sinceTimestamp);
        self::assertGreaterThan(time() - 86400 - 60, $sinceTimestamp);
    }

    #[Test]
    public function pnoteRowsAreNormalizedWithPnoteSource(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow([
                'source' => 'pnote',
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'title' => 'Phone call',
                'snippet' => 'Patient reports cough and fever.',
                'score' => 1.234,
            ]),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertCount(1, $rows);
        self::assertSame('pnote', $rows[0]['source']);
        self::assertSame(5, $rows[0]['id']);
        self::assertSame('2026-04-20 14:30:00', $rows[0]['date']);
        self::assertSame('Phone call', $rows[0]['title']);
        self::assertSame('Patient reports cough and fever.', $rows[0]['snippet']);
        self::assertSame(1.234, $rows[0]['score']);
    }

    #[Test]
    public function clinicalNoteRowsAreNormalizedWithClinicalNoteSource(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow([
                'source' => 'clinical_note',
                'id' => 8,
                'date' => '2026-04-15 00:00:00',
                'title' => 'Progress note',
                'snippet' => 'Discussed cough management.',
                'score' => 0.987,
            ]),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertCount(1, $rows);
        self::assertSame('clinical_note', $rows[0]['source']);
        self::assertSame(8, $rows[0]['id']);
        self::assertSame('2026-04-15 00:00:00', $rows[0]['date']);
        self::assertSame('Progress note', $rows[0]['title']);
        self::assertSame('Discussed cough management.', $rows[0]['snippet']);
        self::assertSame(0.987, $rows[0]['score']);
    }

    #[Test]
    public function emptyStringTextFieldsBecomeNull(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow([
                'title' => '',
                'snippet' => '',
            ]),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertNull($rows[0]['title']);
        self::assertNull($rows[0]['snippet']);
    }

    #[Test]
    public function nullTextFieldsStayNull(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow([
                'title' => null,
                'snippet' => null,
            ]),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertNull($rows[0]['title']);
        self::assertNull($rows[0]['snippet']);
    }

    #[Test]
    public function emptyDateBecomesNull(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow(['date' => '']),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertNull($rows[0]['date']);
    }

    #[Test]
    public function scoreFromMysqlStringIsCoercedToFloat(): void
    {
        // Some MySQL/PDO configurations return DECIMAL/FLOAT columns as
        // strings. The agent expects a real float so it can compare scores.
        $repository = new NotesSearchRepository($this->makeConnection([
            $this->fixtureRow(['score' => '1.234']),
        ]));

        $rows = $repository->search(1, 'cough');

        self::assertSame(1.234, $rows[0]['score']);
    }

    #[Test]
    public function returnsEmptyArrayWhenNoMatchingRows(): void
    {
        $repository = new NotesSearchRepository($this->makeConnection([]));

        $rows = $repository->search(1, 'cough');

        self::assertSame([], $rows);
    }

    /**
     * Build a fixture row matching the post-UNION-alias column shape.
     *
     * @param array<string, mixed> $overrides
     * @return array<string, mixed>
     */
    private function fixtureRow(array $overrides = []): array
    {
        $defaults = [
            'source' => 'pnote',
            'id' => 1,
            'date' => '2026-04-20 14:30:00',
            'title' => 'Phone call',
            'snippet' => 'Patient reports cough.',
            'score' => 1.0,
        ];
        return array_merge($defaults, $overrides);
    }

    /**
     * @param list<array<string, mixed>> $rows
     * @param array<string, mixed>       $captured
     */
    private function makeConnection(array $rows, array &$captured = []): Connection
    {
        $captured = ['sql' => '', 'params' => []];

        $connection = self::createMock(Connection::class);
        $connection
            ->method('fetchAllAssociative')
            ->willReturnCallback(function (string $sql, array $params) use (
                &$captured,
                $rows
            ): array {
                $captured['sql'] = $sql;
                $captured['params'] = $params;
                return $rows;
            });
        return $connection;
    }
}
