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
 * Caching policy (Task 26): a 5-minute private revalidating cache.
 * The citation overlay re-opens the same document on every chip
 * click; on each click PDF.js issues a fresh GET, and re-downloading
 * the bytes (often O(MB)) makes the overlay paint feel laggy. We
 * issue:
 *
 *   ``Cache-Control: max-age=300, private, must-revalidate``
 *   ``ETag: "<md5 of bytes>"``
 *
 * Then on subsequent fetches with ``If-None-Match`` we short-circuit
 * to a 304 (no body). ``private`` is mandatory — these are PHI bytes
 * and a shared (proxy / CDN) cache hit on a previous patient's bytes
 * would invert the JWT-vs-patient scope check. ``must-revalidate``
 * prevents a client from serving stale entries past the freshness
 * window without re-asking the server.
 *
 * The 304 short-circuit runs AFTER JWT validation, document lookup,
 * and the patient-scope check — order is load-bearing, since a 304
 * that bypassed auth would let an attacker who once observed a valid
 * ETag skip authentication entirely.
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
    /**
     * Freshness window before a client must revalidate via
     * ``If-None-Match``. Five minutes is long enough to absorb the
     * citation-overlay re-open burst (a clinician clicks several
     * chips in quick succession) and short enough that a revoked
     * document falls out of cache before the next chart visit.
     */
    private const CACHE_MAX_AGE_SECONDS = 300;

    private const CACHE_CONTROL_VALUE
        = 'max-age=' . self::CACHE_MAX_AGE_SECONDS . ', private, must-revalidate';

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

        // Content-derived strong validator. md5 is fine here — it is
        // not used for security (every request is JWT-gated and
        // patient-scoped above) but as a stable fingerprint of the
        // bytes for cache revalidation. RFC 7232 §2.3 requires the
        // value be quoted; Symfony's Response normalises this for us
        // when we hand it the unquoted hash plus ``weak: false``.
        $etag = md5($document->bytes);

        // Symfony's ``Request::getETags()`` parses the header per
        // RFC 7232 §2.3 and returns the quoted forms. Compare against
        // the quoted version of our hash so wildcards (``*``) and
        // weak/strong distinctions are handled by the framework.
        $quotedEtag = '"' . $etag . '"';
        if (in_array($quotedEtag, $request->getETags(), true)) {
            $notModified = new Response('', Response::HTTP_NOT_MODIFIED);
            $notModified->setEtag($etag);
            $notModified->headers->set('Cache-Control', self::CACHE_CONTROL_VALUE);
            return $notModified;
        }

        $response = new Response(
            $document->bytes,
            Response::HTTP_OK,
            [
                'Content-Type' => $document->mimetype,
                'Content-Length' => (string) strlen($document->bytes),
                'Cache-Control' => self::CACHE_CONTROL_VALUE,
            ]
        );
        $response->setEtag($etag);

        return $response;
    }
}
