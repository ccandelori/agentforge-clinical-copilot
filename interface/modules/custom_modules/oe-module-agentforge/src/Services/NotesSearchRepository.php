<?php

/**
 * NotesSearchRepository — patient-scoped FULLTEXT search across pnotes and
 * form_clinical_notes for the agent's search_notes tool.
 *
 * MVP scope: a single relevance-ranked list across both note tables. The
 * UNION ALL stitches per-source MATCH(...) AGAINST(... IN NATURAL LANGUAGE
 * MODE) projections together; the outer ORDER BY score DESC + LIMIT picks
 * the top hits. NATURAL LANGUAGE MODE is the right default — the user is
 * typing a phrase ("cough" / "knee pain"), not boolean syntax.
 *
 * Filter rules (mirroring NotesRepository):
 *   - pnotes.deleted = 0 — soft-deleted notes are hidden.
 *   - form_clinical_notes.activity = 1 — inactive form drafts are hidden.
 *
 * Schema gotchas:
 *   - pnotes.date is DATETIME; form_clinical_notes.date is DATE. The UNION
 *     coerces both to DATETIME-shaped strings for transport. Date is not
 *     used for ordering here — score is.
 *   - form_clinical_notes' user-facing title lives in `codetext` (the SNOMED
 *     descriptor), and the body lives in `description`. We alias both to
 *     `title` and `snippet` so PHP sees a single column shape across both
 *     sources.
 *   - The snippet is SUBSTRING(body, 1, 200) / SUBSTRING(description, 1, 200)
 *     trimmed at the SQL level. Keeping snippets short cuts JSON payload
 *     size for the agent and avoids accidentally leaking large free-text
 *     bodies through the tool surface.
 *
 * The query relies on ft_pnotes_body / ft_clinical_notes_desc FULLTEXT
 * indexes plus idx_pnotes_pid_date / idx_clinical_notes_pid_date for the
 * pid + date scoping (added by Version20260430000001 / 0002 / 0003).
 *
 * Limit is clamped server-side to [1, 10] — the agent should not pull more
 * than ten search hits in one shot. Days is clamped to [1, 365]; search is
 * intentionally broader than recent_notes because users may search far
 * back into a patient's history.
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

final class NotesSearchRepository
{
    private const MIN_DAYS = 1;
    private const MAX_DAYS = 365;
    private const MIN_LIMIT = 1;
    private const MAX_LIMIT = 10;

    public function __construct(private readonly Connection $connection)
    {
    }

    /**
     * @return list<array{
     *     id: int,
     *     source: string,
     *     date: string|null,
     *     title: string|null,
     *     snippet: string|null,
     *     score: float|null
     * }>
     */
    public function search(int $pid, string $query, int $limit = 5, int $days = 365): array
    {
        $trimmedQuery = trim($query);
        if ($trimmedQuery === '') {
            // Defense in depth: the controller should reject this, but if a
            // caller slips through we refuse to issue MATCH AGAINST('') —
            // it wastes a round trip and may behave unpredictably.
            return [];
        }

        $clampedLimit = max(self::MIN_LIMIT, min(self::MAX_LIMIT, $limit));
        $clampedDays = max(self::MIN_DAYS, min(self::MAX_DAYS, $days));
        $since = (new DateTimeImmutable())
            ->modify("-{$clampedDays} days")
            ->format('Y-m-d H:i:s');

        // The two SELECTs both alias their source-specific columns to a
        // shared shape (`title`, `snippet`, `score`) so PHP receives one
        // uniform row format. The outer ORDER BY / LIMIT live outside the
        // UNION so they apply to the merged ranked result.
        //
        // The clamped-int $clampedLimit is interpolated directly: Doctrine
        // DBAL routes LIMIT through prepared-statement integers via PDO,
        // which on some MySQL drivers binds the value as a string and
        // breaks the syntax. The value is pre-clamped to [1, 10] — no
        // injection surface.
        $sql = "
            SELECT 'pnote' AS source, id, `date`, title,
                   SUBSTRING(body, 1, 200) AS snippet,
                   MATCH(body) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score
            FROM pnotes
            WHERE pid = :pid AND `date` >= :since
              AND deleted = 0
              AND MATCH(body) AGAINST (:q IN NATURAL LANGUAGE MODE)

            UNION ALL

            SELECT 'clinical_note' AS source, id, `date`, codetext AS title,
                   SUBSTRING(description, 1, 200) AS snippet,
                   MATCH(description) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score
            FROM form_clinical_notes
            WHERE pid = :pid AND `date` >= :since
              AND activity = 1
              AND MATCH(description) AGAINST (:q IN NATURAL LANGUAGE MODE)

            ORDER BY score DESC
            LIMIT " . $clampedLimit;

        $rows = $this->connection->fetchAllAssociative(
            $sql,
            ['pid' => $pid, 'q' => $trimmedQuery, 'since' => $since]
        );

        $result = [];
        foreach ($rows as $row) {
            $idRaw = $row['id'] ?? null;
            $sourceRaw = $row['source'] ?? null;
            $dateRaw = $row['date'] ?? null;
            $titleRaw = $row['title'] ?? null;
            $snippetRaw = $row['snippet'] ?? null;
            $scoreRaw = $row['score'] ?? null;

            $score = null;
            if (is_float($scoreRaw) || is_int($scoreRaw)) {
                $score = (float) $scoreRaw;
            } elseif (is_string($scoreRaw) && $scoreRaw !== '' && is_numeric($scoreRaw)) {
                // Some MySQL/PDO configurations return the MATCH score as a
                // numeric string. Coerce so the agent always sees a float.
                $score = (float) $scoreRaw;
            }

            $result[] = [
                'id' => is_int($idRaw) ? $idRaw : (is_string($idRaw) ? (int) $idRaw : 0),
                'source' => is_string($sourceRaw) ? $sourceRaw : 'pnote',
                'date' => is_string($dateRaw) && $dateRaw !== '' ? $dateRaw : null,
                'title' => is_string($titleRaw) && $titleRaw !== '' ? $titleRaw : null,
                'snippet' => is_string($snippetRaw) && $snippetRaw !== ''
                    ? $snippetRaw : null,
                'score' => $score,
            ];
        }
        return $result;
    }
}
