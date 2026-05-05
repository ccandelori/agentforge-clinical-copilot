<?php

/**
 * Value object returned by DocumentBytesRepository — the bytes-and-mime
 * pair the JWT-validated internal endpoint streams back to the sidecar.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

/**
 * Carries the document's owning patient_id alongside bytes/mimetype so
 * the controller can run the JWT-vs-document patient scope check
 * without a second round-trip to storage. The patient_id is the legacy
 * `documents.foreign_id` column.
 */
final readonly class DocumentBytesResult
{
    public function __construct(
        public int $documentId,
        public int $patientId,
        public string $mimetype,
        public string $bytes,
    ) {
    }
}
