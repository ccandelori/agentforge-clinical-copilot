<?php

/**
 * NotesRepository — typed read of pnotes + form_clinical_notes for the
 * agent's get_recent_notes tool.
 *
 * MVP scope: returns the patient's recent notes from two tables in a single
 * normalized shape. pnotes holds free-form patient notes (phone calls, mail,
 * misc messages); form_clinical_notes holds structured encounter notes
 * (progress, history, plan). The agent benefits from seeing both — many
 * encounters are captured in pnotes when the clinical-notes form isn't
 * used, and vice versa.
 *
 * Filter rules:
 *   - pnotes.deleted = 0 — soft-deleted notes are hidden.
 *   - form_clinical_notes.activity = 1 — inactive form drafts are hidden.
 *
 * Schema gotchas:
 *   - pnotes.date is DATETIME; form_clinical_notes.date is DATE. The UNION
 *     coerces both to DATETIME for ORDER BY, so the date column on the
 *     normalized output is consistent string-shaped.
 *   - form_clinical_notes' user-facing title lives in `codetext` (the SNOMED
 *     descriptor), and the body lives in `description`. We alias these to
 *     `title` / `body` in SQL so PHP sees a single column shape across both
 *     sources.
 *
 * The query relies on idx_pnotes_pid_date and idx_clinical_notes_pid_date
 * (added by Version20260430000001 / 0002).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use DateTimeImmutable;
use Doctrine\DBAL\Connection;

class NotesRepository
{
    private const ROW_LIMIT = 50;
    private const MIN_DAYS = 1;
    private const MAX_DAYS = 365;

    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return list<array{
     *     id: int,
     *     source: string,
     *     date: string|null,
     *     author: string|null,
     *     title: string|null,
     *     body: string|null,
     *     note_type: string|null
     * }>
     */
    public function findRecentByPid(int $pid, int $days = 90): array
    {
        $clampedDays = max(self::MIN_DAYS, min(self::MAX_DAYS, $days));
        $since = (new DateTimeImmutable())
            ->modify("-{$clampedDays} days")
            ->format('Y-m-d H:i:s');

        // The two SELECTs both alias their source-specific columns to a
        // shared shape (`title`, `body`, `note_type`) so PHP receives one
        // uniform row format. ORDER BY / LIMIT live outside the UNION so
        // they apply to the merged result.
        $sql = "
            SELECT 'pnote' AS source, id, `date`, user AS author,
                   title, body, NULL AS note_type
            FROM pnotes
            WHERE pid = :pid AND `date` >= :since AND deleted = 0

            UNION ALL

            SELECT 'clinical_note' AS source, id, `date`, user AS author,
                   codetext AS title, description AS body,
                   clinical_notes_type AS note_type
            FROM form_clinical_notes
            WHERE pid = :pid AND `date` >= :since AND activity = 1

            ORDER BY `date` DESC
            LIMIT " . self::ROW_LIMIT;

        $rows = $this->connection->fetchAllAssociative(
            $sql,
            ['pid' => $pid, 'since' => $since]
        );

        $result = [];
        foreach ($rows as $row) {
            $idRaw = $row['id'] ?? null;
            $sourceRaw = $row['source'] ?? null;
            $dateRaw = $row['date'] ?? null;
            $authorRaw = $row['author'] ?? null;
            $titleRaw = $row['title'] ?? null;
            $bodyRaw = $row['body'] ?? null;
            $noteTypeRaw = $row['note_type'] ?? null;

            $result[] = [
                'id' => is_int($idRaw) ? $idRaw : (is_string($idRaw) ? (int) $idRaw : 0),
                'source' => is_string($sourceRaw) ? $sourceRaw : 'pnote',
                'date' => is_string($dateRaw) && $dateRaw !== '' ? $dateRaw : null,
                'author' => is_string($authorRaw) && $authorRaw !== ''
                    ? $authorRaw : null,
                'title' => is_string($titleRaw) && $titleRaw !== '' ? $titleRaw : null,
                'body' => is_string($bodyRaw) && $bodyRaw !== '' ? $bodyRaw : null,
                'note_type' => is_string($noteTypeRaw) && $noteTypeRaw !== ''
                    ? $noteTypeRaw : null,
            ];
        }
        return $result;
    }
}
