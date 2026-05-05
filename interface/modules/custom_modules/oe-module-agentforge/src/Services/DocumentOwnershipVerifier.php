<?php

/**
 * DocumentOwnershipVerifier — single-purpose query for the
 * "which patient owns this document?" check used by the internal
 * persistence endpoints (intake form Task 12, lab result Task 8).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use Doctrine\DBAL\Connection;

/**
 * Returns the `documents.foreign_id` (which is the owning patient_id)
 * for a given document, or null if the document doesn't exist or has
 * been deleted (`deleted = 1`).
 *
 * The internal persistence endpoints use this for the third leg of
 * the JWT.patient_id == request.patient_id == documents.foreign_id
 * triple-check that defends against JWT replay + forged document_id
 * attacks. A null return MUST be treated as a 403 by callers — the
 * check is "this patient owns this document"; a missing or deleted
 * document fails the check just the same as an explicit ownership
 * mismatch.
 *
 * The verifier deliberately returns null on "deleted" rather than
 * raising; deleted-document and missing-document are observationally
 * indistinguishable to a caller (which is the right disclosure
 * stance — the API shouldn't tell the agent "this document USED to
 * exist but has been deleted").
 */
readonly class DocumentOwnershipVerifier
{
    public function __construct(
        private Connection $connection,
    ) {
    }

    /**
     * @return positive-int|null `documents.foreign_id` if the row exists
     *         and is not deleted, null otherwise.
     */
    public function findOwningPatientId(int $documentId): ?int
    {
        if ($documentId <= 0) {
            return null;
        }

        $row = $this->connection->fetchOne(
            'SELECT foreign_id FROM documents WHERE id = :id AND deleted = 0',
            ['id' => $documentId],
        );

        if (!is_int($row) && !is_numeric($row)) {
            // Not found, deleted, or null `foreign_id` (data oddity).
            return null;
        }

        $foreignId = (int) $row;
        return $foreignId > 0 ? $foreignId : null;
    }
}
