<?php

/**
 * DocumentUploadWriter — wraps the legacy ``Document`` class for the
 * AgentForge upload flow (Task 6). Translates the ``createDocument()``
 * "empty string on success" convention into a typed return / throw,
 * looks up the right category by name, and keeps the caller (the
 * sidecar-facing :class:`InternalUploadDocumentController`) free of
 * legacy quirks.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use Closure;
use Document;
use Doctrine\DBAL\Connection;
use RuntimeException;

/**
 * Two-step API: ``upload(...)`` resolves the category, instantiates a
 * fresh ``Document`` via the injected factory, calls
 * ``createDocument()``, and returns the new document id. Errors in the
 * legacy class surface as :class:`RuntimeException` so the controller
 * gets a single failure shape to map to HTTP 500.
 *
 * The :class:`Document` instantiation goes through a Closure factory
 * (``fn () => new Document()`` in production) rather than a direct
 * ``new`` call. That's the seam tests use to swap in an in-memory
 * stub — :class:`Document` reads :class:`OpenEMR\Core\OEGlobalsBag`
 * during construction, so we can't ``new`` it inside an isolated
 * unit test.
 */
readonly class DocumentUploadWriter
{
    /**
     * Closed mapping from the ``doc_type`` enum (Task 6 spec) to the
     * stock OpenEMR category names. Looking up by name instead of
     * hard-coding the integer id keeps the writer portable across
     * deployments where the categories table has been re-numbered.
     */
    private const DOC_TYPE_TO_CATEGORY_NAME = [
        'lab_pdf' => 'Lab Report',
        'intake_form' => 'Patient Information',
    ];

    /**
     * @param Closure(): Document $documentFactory
     */
    public function __construct(
        private Connection $connection,
        private Closure $documentFactory,
    ) {
    }

    /**
     * Upload one document and return its new ``id``. Throws
     * :class:`RuntimeException` on any failure path:
     *
     * - Unsupported ``doc_type`` (caller should validate before this).
     * - Category missing from the deployment.
     * - Legacy ``Document::createDocument`` returns a non-empty error
     *   string (CouchDB / filesystem / encryption failure).
     * - Document didn't get an id post-create (write-storage failure).
     */
    public function upload(
        int $patientId,
        string $docType,
        string $filename,
        string $mimetype,
        string $bytes,
        int $ownerUserId,
        ?int $encounterId = null,
    ): int {
        if (!isset(self::DOC_TYPE_TO_CATEGORY_NAME[$docType])) {
            throw new RuntimeException(
                "Unsupported doc_type: {$docType}",
            );
        }
        $categoryName = self::DOC_TYPE_TO_CATEGORY_NAME[$docType];
        $categoryId = $this->resolveCategoryId($categoryName);

        // Document::createDocument takes $data by reference; the legacy
        // class will mutate it (e.g. encryption). We don't reuse the
        // buffer afterwards so that's harmless here, but we still bind
        // a local variable to satisfy the by-reference signature.
        $data = $bytes;

        $doc = ($this->documentFactory)();
        // Document::createDocument is documented as @param string for
        // patient_id even though numeric ids end up in the same column;
        // cast at the seam.
        $error = $doc->createDocument(
            (string) $patientId,
            $categoryId,
            $filename,
            $mimetype,
            $data,
            '',
            1,
            $ownerUserId,
            null,
            null,
            null,
            null,
            $encounterId !== null ? (string) $encounterId : '',
        );

        // Documented @return string. Treat any non-empty as failure;
        // PHPStan trusts the docblock so we don't re-check the type.
        if ($error !== '') {
            throw new RuntimeException(
                "Document::createDocument failed: {$error}",
            );
        }

        $documentId = $doc->get_id();
        if (!is_int($documentId) || $documentId <= 0) {
            throw new RuntimeException(
                'Document::createDocument succeeded but get_id returned no id',
            );
        }

        return $documentId;
    }

    private function resolveCategoryId(string $categoryName): int
    {
        $row = $this->connection->fetchOne(
            'SELECT id FROM categories WHERE name = ? LIMIT 1',
            [$categoryName],
        );

        if (is_int($row) && $row > 0) {
            return $row;
        }
        if (is_string($row) && ctype_digit($row)) {
            return (int) $row;
        }

        throw new RuntimeException(
            "Document category '{$categoryName}' is not seeded in this database",
        );
    }
}
