<?php

/**
 * DocumentBytesRepository — loads a single document's metadata and bytes
 * for the JWT-validated internal endpoint.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use BadMethodCallException;
use Document;
use RuntimeException;

/**
 * Wraps the legacy `Document` class so the controller layer can stay
 * unit-testable (existing legacy class is hard to mock; this repository
 * is trivial to mock). The repository returns:
 *
 *   - null if the document_id doesn't resolve to a record (404 case)
 *   - a {@see DocumentBytesResult} otherwise (controller decides 200 vs
 *     403 by comparing patientId against the JWT's claim)
 *
 * Bytes are loaded eagerly here. The persistence flow is one-shot —
 * the sidecar requests bytes once per upload, the bytes flow through
 * memory, no server-side caching. Splitting metadata-then-bytes would
 * save a read for unauthorized requests but adds a round-trip class on
 * the auth path that's almost always authorized; not worth the
 * complexity at this scale.
 */
class DocumentBytesRepository
{
    public function findById(int $documentId): ?DocumentBytesResult
    {
        if ($documentId <= 0) {
            return null;
        }

        // The legacy Document constructor accepts an id and populates
        // fields lazily; an unresolved record leaves get_id() returning
        // null or 0. The accessors are mixed-typed in the legacy class,
        // so we narrow rather than cast (CLAUDE.md: "Narrow, don't cast").
        $document = new Document($documentId);
        $resolvedId = $document->get_id();
        if (!self::isPositiveInt($resolvedId) || $resolvedId !== $documentId) {
            // No matching row in `documents`.
            return null;
        }

        // `documents.foreign_id` is the patient_id by convention in this
        // fork. A document without a recorded patient owner is a data-
        // integrity oddity, not a 200-eligible payload — treat as missing.
        $foreignId = $document->get_foreign_id();
        if (!self::isPositiveInt($foreignId)) {
            return null;
        }

        $mimetype = $document->get_mimetype();
        if (!is_string($mimetype) || $mimetype === '') {
            // A document without a recorded mime is exotic; fall back
            // to a binary default rather than 500ing the response.
            $mimetype = 'application/octet-stream';
        }

        // Document::get_data() throws on the legacy storage edge cases:
        //   - BadMethodCallException for expired or deleted documents,
        //     and for documents not stored on the filesystem
        //   - RuntimeException for missing files on disk and for
        //     decryption failures
        // Treat all of these as "the bytes aren't retrievable", which is
        // observationally indistinguishable from a missing record from
        // the caller's perspective. Returning null routes them to a 404
        // rather than letting them bubble up as an uncaught 500.
        //
        // PHPStan note: get_data()'s docblock only declares
        // BadMethodCallException, but in practice get_content_from_filesystem
        // throws RuntimeException for missing files on disk and that
        // propagates undeclared. Catching it anyway is correct in
        // production; the suppression below silences phpstan's
        // documentation-trusting catch.neverThrown rule.
        try {
            $bytes = $document->get_data();
        /** @phpstan-ignore catch.neverThrown */
        } catch (BadMethodCallException | RuntimeException) {
            return null;
        }
        if (!is_string($bytes)) {
            // Belt-and-suspenders for any other legacy quirk that
            // returns a non-string instead of throwing.
            return null;
        }

        return new DocumentBytesResult(
            documentId: $documentId,
            patientId: $foreignId,
            mimetype: $mimetype,
            bytes: $bytes,
        );
    }

    /**
     * @phpstan-assert-if-true positive-int $value
     */
    private static function isPositiveInt(mixed $value): bool
    {
        return is_int($value) && $value > 0;
    }
}
