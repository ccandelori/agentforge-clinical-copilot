<?php

declare(strict_types=1);

/**
 * UploadDocumentController — receives browser POSTs to
 * `/agentforge/upload_document`, validates session + patient context,
 * stores the uploaded PDF via the legacy `Document` store, and fires
 * the `agentforge.document_ingest` audit event on success.
 *
 * The endpoint is the user-facing complement to the JWT-validated
 * sidecar→OpenEMR endpoints: the browser uploads here, the sidecar's
 * vision tool later pulls the document bytes via
 * `internal/get_document_bytes.php`. Closing this loop is what makes
 * Task 11 / Task 13's vision pipeline run on real user-uploaded PDFs
 * rather than only the bundled mock fixtures.
 *
 * **Security-critical: patient_id is derived from the active OpenEMR
 * session (`pid`), not from the multipart payload.** The payload may
 * carry a `patient_id` field for client-side convenience (some demo
 * forms post it as a sanity duplicate), but if it disagrees with the
 * session pid we reject with 400 — this prevents a malicious script
 * with a valid session from cross-uploading to a chart it shouldn't
 * see. Mismatch is a client error, not an auditable action: the
 * audit-log discipline is "one row per *successful* upload," so we
 * deliberately do NOT fire the audit on this rejection path.
 *
 * Failure modes mapped explicitly to HTTP status codes:
 *   400  multipart validation (no file, bad doc_type, mismatched pid)
 *   400  no patient context (open a chart first)
 *   401  no auth (user not logged in)
 *   500  document store / audit failure
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Controllers;

use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use RuntimeException;
use Symfony\Component\HttpFoundation\File\UploadedFile;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class UploadDocumentController
{
    /**
     * Allowed values for the multipart ``doc_type`` field. The pair
     * mirrors the :class:`VisionContract` set in the sidecar
     * (``LAB_CONTRACT`` / ``INTAKE_CONTRACT``); adding a third type is
     * a coordinated change with the sidecar's
     * :mod:`agentforge.tools.attach_and_extract`.
     */
    private const ALLOWED_DOC_TYPES = ['lab_pdf', 'intake_form'];

    private const PDF_MIME = 'application/pdf';

    /**
     * Soft cap on the per-upload PDF size. The actual hard limits live
     * in PHP's ``upload_max_filesize`` / ``post_max_size`` and the
     * webserver config; this constant is the application-layer
     * ceiling that gives a clear 400 (with an explanatory message)
     * rather than a generic web-server failure on large uploads.
     */
    private const MAX_BYTES = 25 * 1024 * 1024; // 25 MB

    public function __construct(
        private readonly DocumentUploadWriter $writer,
        private readonly DocumentIngestAuditWriter $auditWriter,
    ) {
    }

    public function upload(Request $request): JsonResponse
    {
        $session = $request->getSession();

        $userId = $session->get('authUserID');
        $username = $session->get('authUser');
        if (!is_int($userId) || $userId <= 0 || !is_string($username) || $username === '') {
            return new JsonResponse(
                ['error' => 'Authentication required.'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        $sessionPatientId = $session->get('pid');
        if (!is_int($sessionPatientId) || $sessionPatientId <= 0) {
            return new JsonResponse(
                [
                    'error' => 'No patient context. Open a patient chart before uploading.',
                ],
                Response::HTTP_BAD_REQUEST,
            );
        }

        // Multipart fields. Symfony's request->request bag holds
        // POST text fields; files live in request->files.
        $docType = $request->request->get('doc_type');
        if (!is_string($docType) || !in_array($docType, self::ALLOWED_DOC_TYPES, true)) {
            return new JsonResponse(
                [
                    'error' => sprintf(
                        "doc_type is required and must be one of: %s.",
                        implode(', ', self::ALLOWED_DOC_TYPES),
                    ),
                ],
                Response::HTTP_BAD_REQUEST,
            );
        }

        // payload patient_id is a client-side convenience; if present,
        // it MUST match the session pid. Mismatch is a client error
        // (no audit event fired — see class docblock).
        $payloadPatientIdRaw = $request->request->get('patient_id');
        if ($payloadPatientIdRaw !== null && $payloadPatientIdRaw !== '') {
            $payloadPatientId = filter_var($payloadPatientIdRaw, FILTER_VALIDATE_INT);
            if ($payloadPatientId === false || $payloadPatientId !== $sessionPatientId) {
                return new JsonResponse(
                    [
                        'error' => 'Patient ID mismatch. The uploaded patient_id does not match the active chart.',
                    ],
                    Response::HTTP_BAD_REQUEST,
                );
            }
        }

        $encounterIdRaw = $request->request->get('encounter_id');
        $encounterId = null;
        if ($encounterIdRaw !== null && $encounterIdRaw !== '') {
            $candidate = filter_var($encounterIdRaw, FILTER_VALIDATE_INT);
            if ($candidate === false || $candidate <= 0) {
                return new JsonResponse(
                    ['error' => 'encounter_id, if provided, must be a positive integer.'],
                    Response::HTTP_BAD_REQUEST,
                );
            }
            $encounterId = $candidate;
        }

        $file = $request->files->get('file');
        if (!$file instanceof UploadedFile || !$file->isValid()) {
            return new JsonResponse(
                ['error' => 'A PDF file is required (multipart field "file").'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $size = $file->getSize();
        if ($size === false || $size <= 0) {
            return new JsonResponse(
                ['error' => 'Uploaded file is empty.'],
                Response::HTTP_BAD_REQUEST,
            );
        }
        if ($size > self::MAX_BYTES) {
            return new JsonResponse(
                [
                    'error' => sprintf(
                        'Uploaded file exceeds %d-byte limit.',
                        self::MAX_BYTES,
                    ),
                ],
                Response::HTTP_BAD_REQUEST,
            );
        }

        // PDF detection via magic bytes (the first 5 bytes of any PDF
        // are the literal ``%PDF-``). UploadedFile->getMimeType reads
        // the ``finfo`` magic database which is the same check; do it
        // here ourselves so a misconfigured ``finfo`` doesn't silently
        // 400 every legitimate upload. Reading 5 bytes from the temp
        // file is cheap and unambiguous.
        $tmpPath = $file->getRealPath();
        if (!is_string($tmpPath) || $tmpPath === '') {
            return new JsonResponse(
                ['error' => 'Upload could not be read from the temporary path.'],
                Response::HTTP_BAD_REQUEST,
            );
        }
        $magic = file_get_contents($tmpPath, length: 5);
        if (!is_string($magic) || !str_starts_with($magic, '%PDF-')) {
            return new JsonResponse(
                ['error' => 'Uploaded file is not a valid PDF (magic-byte check failed).'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $bytes = file_get_contents($tmpPath);
        if (!is_string($bytes) || $bytes === '') {
            return new JsonResponse(
                ['error' => 'Upload could not be read.'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $clientName = $file->getClientOriginalName();
        $filename = $clientName !== '' ? $clientName : ('upload-' . bin2hex(random_bytes(8)) . '.pdf');

        try {
            $documentId = $this->writer->upload(
                patientId: $sessionPatientId,
                docType: $docType,
                filename: $filename,
                mimetype: self::PDF_MIME,
                bytes: $bytes,
                ownerUserId: $userId,
                encounterId: $encounterId,
            );
        } catch (RuntimeException) {
            // Don't echo the legacy class's error message — it can
            // include filesystem paths or storage backend details
            // that are operational PHI exposure risks. The exception
            // chain is preserved in the global handler's logs.
            return new JsonResponse(
                ['error' => 'Upload failed; the document store rejected the request.'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        $breakglassFlag = $session->get('breakglass_flag', false) === true;
        $breakglassReasonRaw = $session->get('breakglass_reason');
        $breakglassReason = is_string($breakglassReasonRaw) ? $breakglassReasonRaw : null;

        // Audit-write failures propagate. The audit row is the legal
        // trail; if the database can't write it, returning 200 to the
        // client would silently break that trail. In practice, the
        // audit DB is the same MySQL instance as the document store,
        // so a failure here implies the document write would have
        // failed too — this exception is rare-but-fatal, not
        // recoverable.
        $this->auditWriter->record(
            userId: $userId,
            patientId: $sessionPatientId,
            documentId: $documentId,
            docType: $docType,
            breakglassFlag: $breakglassFlag,
            breakglassReason: $breakglassReason,
        );

        return new JsonResponse(
            [
                'success' => true,
                'document_id' => $documentId,
            ],
            Response::HTTP_CREATED,
        );
    }
}
