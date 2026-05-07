<?php

/**
 * InternalUploadDocumentController — sidecar-facing JWT-authed multipart
 * upload endpoint (T38.15). Receives PDF bytes from the BFF (vue-ui →
 * sidecar BFF → here) and lands them in the OpenEMR document store via
 * the existing :class:`DocumentUploadWriter`, then fires the
 * ``agentforge.document_ingest`` audit event on success.
 *
 * The session-authed sibling is :class:`UploadDocumentController`; both
 * delegate the actual write to :class:`DocumentUploadWriter` so the
 * legacy ``Document::createDocument`` quirks are confined to one place.
 *
 * **Patient-scope authority lives on the JWT, not the multipart
 * payload.** The BFF resolves the patient UUID into the integer pid
 * server-side and bakes it into the JWT's ``patient_id`` claim before
 * signing — a tampered multipart ``patient_uuid`` cannot widen scope
 * because we resolve it again here and refuse the upload when the
 * resolved pid does not match the JWT's claim. Same posture as
 * :class:`InternalDocumentBytesController`.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Controllers;

use Lcobucci\JWT\Exception as JwtException;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\File\UploadedFile;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * The endpoint accepts a multipart body (``file``, ``patient_uuid``,
 * ``doc_type``, optional ``encounter_id``) plus a Bearer JWT.
 *
 * Failure modes mapped to HTTP status codes:
 *
 *   401 — missing / malformed / expired JWT (or wrong-secret signature)
 *   400 — missing ``patient_uuid``, missing/invalid file, missing /
 *         unsupported ``doc_type``, file is not a PDF (magic-byte fail)
 *   404 — ``patient_uuid`` resolves to no row in ``patient_data``
 *   403 — resolved pid mismatches the JWT's ``patient_id`` claim
 *   500 — :class:`DocumentUploadWriter` throws (legacy storage failure;
 *         the legacy class's error message is intentionally NOT echoed
 *         because it can include filesystem paths)
 *   200 — happy path; body is ``{"document_id": int}``
 *
 * 200 (not 201) keeps the response shape interchangeable with the
 * sidecar ``DocumentBytesFetchError`` typed-error pattern: the BFF
 * route reads ``document_id`` and returns it to the browser.
 */
class InternalUploadDocumentController
{
    /**
     * Mirrors :class:`UploadDocumentController`'s allowed set so the
     * two upload paths accept the same documents. Adding a third
     * type is a coordinated change with the sidecar's vision tools.
     */
    private const ALLOWED_DOC_TYPES = ['lab_pdf', 'intake_form'];

    private const PDF_MIME = 'application/pdf';

    /**
     * Soft cap on the per-upload PDF size. The BFF route enforces an
     * earlier (smaller) cap; this is the application-layer ceiling
     * that gives a clean 400 if the BFF cap is misconfigured.
     */
    private const MAX_BYTES = 25 * 1024 * 1024; // 25 MB

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly PatientPidRepository $patientRepository,
        private readonly DocumentUploadWriter $writer,
        private readonly DocumentIngestAuditWriter $auditWriter,
    ) {
    }

    public function upload(Request $request): JsonResponse
    {
        // ---- 1. JWT validation (401 paths) -----------------------
        $authHeader = $request->headers->get('Authorization');
        if ($authHeader === null || $authHeader === '') {
            return new JsonResponse(
                ['error' => 'Authorization header is required'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        try {
            $claims = $this->validator->validateBearer($authHeader);
        } catch (JwtException | RuntimeException) {
            return new JsonResponse(
                ['error' => 'Invalid or expired token'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        // ---- 2. Multipart parsing (400 paths) --------------------
        $patientUuid = $request->request->get('patient_uuid');
        if (!is_string($patientUuid) || $patientUuid === '') {
            return new JsonResponse(
                ['error' => 'patient_uuid multipart field is required'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $docType = $request->request->get('doc_type');
        if (!is_string($docType) || !in_array($docType, self::ALLOWED_DOC_TYPES, true)) {
            return new JsonResponse(
                [
                    'error' => sprintf(
                        'doc_type is required and must be one of: %s.',
                        implode(', ', self::ALLOWED_DOC_TYPES),
                    ),
                ],
                Response::HTTP_BAD_REQUEST,
            );
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

        // PDF magic-byte check mirrors UploadDocumentController. A
        // misconfigured ``finfo`` would otherwise turn every legitimate
        // upload into a 400; reading 5 bytes from the temp file is the
        // unambiguous answer.
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

        // ---- 3. UUID → pid resolution (404 + 403 paths) ----------
        $resolvedPid = $this->patientRepository->findPidByUuid($patientUuid);
        if ($resolvedPid === null) {
            return new JsonResponse(
                ['error' => 'No OpenEMR patient found for the given UUID'],
                Response::HTTP_NOT_FOUND,
            );
        }

        // The load-bearing privacy invariant: a sidecar bug that mints
        // a JWT for patient A but uploads with patient B's UUID must
        // land 403, never 200. The BFF resolves the UUID server-side
        // before minting the JWT, so a mismatch here implies tampering
        // or a routing bug — log it loud, deny it cold.
        if ($resolvedPid !== $claims->patientId) {
            return new JsonResponse(
                ['error' => 'Document upload does not belong to the authenticated patient'],
                Response::HTTP_FORBIDDEN,
            );
        }

        // ---- 4. Read bytes + delegate (200 / 500 paths) ----------
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
                patientId: $claims->patientId,
                docType: $docType,
                filename: $filename,
                mimetype: self::PDF_MIME,
                bytes: $bytes,
                ownerUserId: $claims->userId,
                encounterId: $encounterId,
            );
        } catch (RuntimeException) {
            // Mirror UploadDocumentController: the legacy class's error
            // message can include filesystem paths or storage backend
            // details, both PHI exposure risks. The exception chain is
            // preserved in the global handler's logs.
            return new JsonResponse(
                ['error' => 'Upload failed; the document store rejected the request.'],
                Response::HTTP_INTERNAL_SERVER_ERROR,
            );
        }

        // ---- 5. Audit + 200 -------------------------------------
        // Internal endpoint has no breakglass session context; the
        // breakglass flag/reason for sidecar-mediated writes lives on
        // the BFF turn record, not on per-document audits.
        $this->auditWriter->record(
            userId: $claims->userId,
            patientId: $claims->patientId,
            documentId: $documentId,
            docType: $docType,
            breakglassFlag: false,
            breakglassReason: null,
        );

        return new JsonResponse(
            ['document_id' => $documentId],
            Response::HTTP_OK,
        );
    }
}
