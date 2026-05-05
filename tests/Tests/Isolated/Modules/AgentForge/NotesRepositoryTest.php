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
use OpenEMR\Modules\AgentForge\Services\NotesRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for NotesRepository.
 *
 * The repository UNIONs two tables with different schemas — pnotes (free-form
 * patient notes) and form_clinical_notes (structured encounter notes) — into
 * a single normalized result. Tests pin both the SQL shape (filters,
 * ordering, lookback window) and the per-source value mapping the agent will
 * see.
 */
final class NotesRepositoryTest extends TestCase
{
    #[Test]
    public function findRecentByPidQueriesBothTables(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(123, 30);

        self::assertStringContainsString('FROM pnotes', $captured['sql']);
        self::assertStringContainsString('FROM form_clinical_notes', $captured['sql']);
        self::assertStringContainsString('UNION ALL', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidFiltersDeletedAndInactive(): void
    {
        // pnotes uses `deleted = 0` for live rows; form_clinical_notes uses
        // `activity = 1`. Both filters MUST be in the SQL — without them the
        // agent would see soft-deleted notes and inactive form drafts.
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(1, 30);

        self::assertStringContainsString('deleted = 0', $captured['sql']);
        self::assertStringContainsString('activity = 1', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidOrdersByDateDescAndLimits(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(1, 30);

        self::assertStringContainsString('ORDER BY', $captured['sql']);
        self::assertStringContainsString('DESC', $captured['sql']);
        self::assertStringContainsString('LIMIT 50', $captured['sql']);
    }

    #[Test]
    public function findRecentByPidBindsPidAndSinceParameters(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(123, 30);

        $params = $captured['params'];
        self::assertSame(123, $params['pid']);
        self::assertMatchesRegularExpression(
            '/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/',
            $params['since'],
        );
    }

    #[Test]
    public function findRecentByPidClampsExcessiveDaysToMaximum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(1, 99999);

        // The clamp should cap at 365 days. Since timestamp should be no
        // earlier than that.
        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        $maxLookbackSeconds = 365 * 86400;
        self::assertGreaterThanOrEqual(
            time() - $maxLookbackSeconds - 60,
            $sinceTimestamp,
        );
    }

    #[Test]
    public function findRecentByPidClampsZeroOrNegativeDaysToMinimum(): void
    {
        $captured = [];
        $connection = $this->makeConnection(rows: [], captured: $captured);
        $repository = new NotesRepository($connection);

        $repository->findRecentByPid(1, 0);

        // Zero days clamps to 1 — since is roughly "yesterday."
        $sinceTimestamp = strtotime($captured['params']['since']);
        self::assertNotFalse($sinceTimestamp);
        self::assertLessThan(time(), $sinceTimestamp);
        self::assertGreaterThan(time() - 86400 - 60, $sinceTimestamp);
    }

    #[Test]
    public function pnoteRowsAreNormalizedWithPnoteSource(): void
    {
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow([
                'source' => 'pnote',
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'author' => 'dr.smith',
                'title' => 'Phone call',
                'body' => 'Patient reports improvement.',
                'note_type' => null,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertCount(1, $rows);
        self::assertSame('pnote', $rows[0]['source']);
        self::assertSame(5, $rows[0]['id']);
        self::assertSame('2026-04-20 14:30:00', $rows[0]['date']);
        self::assertSame('dr.smith', $rows[0]['author']);
        self::assertSame('Phone call', $rows[0]['title']);
        self::assertSame('Patient reports improvement.', $rows[0]['body']);
        self::assertNull($rows[0]['note_type']);
    }

    #[Test]
    public function clinicalNoteRowsAreNormalizedWithClinicalNoteSource(): void
    {
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow([
                'source' => 'clinical_note',
                'id' => 8,
                'date' => '2026-04-15 00:00:00',
                'author' => 'dr.jones',
                'title' => 'Progress note',
                'body' => 'Discussed care plan.',
                'note_type' => 'progress',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertCount(1, $rows);
        self::assertSame('clinical_note', $rows[0]['source']);
        self::assertSame(8, $rows[0]['id']);
        self::assertSame('2026-04-15 00:00:00', $rows[0]['date']);
        self::assertSame('dr.jones', $rows[0]['author']);
        self::assertSame('Progress note', $rows[0]['title']);
        self::assertSame('Discussed care plan.', $rows[0]['body']);
        self::assertSame('progress', $rows[0]['note_type']);
    }

    #[Test]
    public function emptyStringTextFieldsBecomeNull(): void
    {
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow([
                'author' => '',
                'title' => '',
                'body' => '',
                'note_type' => '',
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['author']);
        self::assertNull($rows[0]['title']);
        self::assertNull($rows[0]['body']);
        self::assertNull($rows[0]['note_type']);
    }

    #[Test]
    public function nullTextFieldsStayNull(): void
    {
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow([
                'author' => null,
                'title' => null,
                'body' => null,
                'note_type' => null,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['author']);
        self::assertNull($rows[0]['title']);
        self::assertNull($rows[0]['body']);
        self::assertNull($rows[0]['note_type']);
    }

    #[Test]
    public function returnsEmptyArrayWhenNoRowsFound(): void
    {
        $repository = new NotesRepository($this->makeConnection([]));

        $rows = $repository->findRecentByPid(1);

        self::assertSame([], $rows);
    }

    #[Test]
    public function emptyDateBecomesNull(): void
    {
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow(['date' => '']),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertNull($rows[0]['date']);
    }

    #[Test]
    public function bothSourcesCanCoexistInResult(): void
    {
        // The repository receives rows in the order MySQL emits them.
        // The ORDER BY in the SQL governs sort; we don't re-sort here.
        $repository = new NotesRepository($this->makeConnection([
            $this->fixtureRow([
                'source' => 'clinical_note',
                'id' => 8,
                'date' => '2026-04-25 00:00:00',
                'title' => 'Visit summary',
                'note_type' => 'progress',
            ]),
            $this->fixtureRow([
                'source' => 'pnote',
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'title' => 'Phone call',
                'note_type' => null,
            ]),
        ]));

        $rows = $repository->findRecentByPid(1);

        self::assertCount(2, $rows);
        self::assertSame('clinical_note', $rows[0]['source']);
        self::assertSame('pnote', $rows[1]['source']);
    }

    /**
     * Build a fixture row matching the post-UNION-alias column shape.
     * Both pnote and clinical_note rows arrive with the same keys; the
     * SQL aliases handle the per-table column-name mapping.
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
            'author' => 'dr.smith',
            'title' => 'Phone call',
            'body' => 'Note body.',
            'note_type' => null,
        ];
        return array_merge($defaults, $overrides);
    }

    /**
     * @param list<array<string, mixed>>                                     $rows
     * @param array<empty>                                                   $captured
     * @param-out array{sql: string, params: array{pid: int, since: string}} $captured
     *
     * The fetchAllAssociative mock writes the executed SQL and bound params
     * into $captured (out-parameter) so each test can assert on the wire-level
     * query shape. The @param-out shape is what callers see after the call;
     * input is always an empty array.
     */
    private function makeConnection(array $rows, array &$captured = []): Connection
    {
        // Dummy init matching the @param-out shape so PHPStan sees a
        // consistent type at every program point. Real values come in
        // when fetchAllAssociative fires below.
        $captured = ['sql' => '', 'params' => ['pid' => 0, 'since' => '']];

        $connection = self::createMock(Connection::class);
        $connection
            ->method('fetchAllAssociative')
            ->willReturnCallback(function (string $sql, array $params) use (
                &$captured,
                $rows
            ): array {
                /** @var array{pid: int, since: string} $params */
                $captured = ['sql' => $sql, 'params' => $params];
                return $rows;
            });
        return $connection;
    }
}
