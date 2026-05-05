<?php

/**
 * InternalDocumentBytesController — sidecar-facing read-only endpoint
 * that streams a document's raw bytes back to the agent for vision
 * extraction (lab PDFs and intake forms).
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
use OpenEMR\Modules\AgentForge\Services\DocumentBytesRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * The endpoint accepts a `document_id` query param and returns the
 * stored bytes if and only if the document's owning patient matches
 * the JWT's `patient_id` claim. Three failure modes:
 *
 *   401 — missing / malformed / expired JWT
 *   400 — no `document_id` query param (or non-positive)
 *   404 — `document_id` doesn't resolve to a record
 *   403 — record exists but its `foreign_id` (patient owner) doesn't
 *         match the JWT's `patient_id` claim
 *
 * Bytes are streamed verbatim; no server-side caching. The sidecar's
 * vision tool consumes one document per call, the response body is
 * already the smallest privacy-relevant chunk we can return, and any
 * cache layer would invert the load-bearing patient-scope check.
 *
 * The matching JWT-patient-vs-document check duplicates the same kind
 * of triple-validation Task 12's persistence endpoint will do
 * (`JWT == request payload == document owner`). For this read-only
 * endpoint there is no separate "request payload" — the JWT and the
 * document_id together describe the request — so the check is a
 * double-validation: claim's patient_id vs the document's foreign_id.
 */
class InternalDocumentBytesController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly DocumentBytesRepository $repository,
    ) {
    }

    public function show(Request $request): Response
    {
        $authHeader = $request->headers->get('Authorization');
        if ($authHeader === null || $authHeader === '') {
            return new JsonResponse(
                ['error' => 'Authorization header is required'],
                Response::HTTP_UNAUTHORIZED
            );
        }

        try {
            $claims = $this->validator->validateBearer($authHeader);
        } catch (JwtException | RuntimeException $e) {
            return new JsonResponse(
                ['error' => 'Invalid or expired token'],
                Response::HTTP_UNAUTHORIZED
            );
        }

        $documentId = $request->query->getInt('document_id', 0);
        if ($documentId <= 0) {
            return new JsonResponse(
                ['error' => 'document_id query parameter is required and must be positive'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $document = $this->repository->findById($documentId);
        if ($document === null) {
            return new JsonResponse(
                ['error' => 'Document not found'],
                Response::HTTP_NOT_FOUND
            );
        }

        if ($document->patientId !== $claims->patientId) {
            // The document exists but is owned by a different patient.
            // Returning 403 (rather than 404) is intentional — the JWT
            // already authenticated a real user; we're refusing
            // authorization, not denying existence. A 404-instead-of-403
            // disclosure scheme would matter for an unauthenticated
            // surface, not this one.
            return new JsonResponse(
                ['error' => 'Document does not belong to the authenticated patient'],
                Response::HTTP_FORBIDDEN
            );
        }

        $response = new Response(
            $document->bytes,
            Response::HTTP_OK,
            [
                'Content-Type' => $document->mimetype,
                'Content-Length' => (string) strlen($document->bytes),
                // Defense in depth: forbid any intermediary cache from
                // retaining the bytes. The JWT scopes the request, so
                // a shared cache hit on a previous patient's bytes
                // would be a privacy regression.
                'Cache-Control' => 'no-store, no-cache, must-revalidate, private',
            ]
        );

        return $response;
    }
}
