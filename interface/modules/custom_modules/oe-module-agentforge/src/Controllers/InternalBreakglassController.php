<?php

/**
 * InternalBreakglassController — sidecar-facing write endpoint that
 * records a breakglass audit event when a breakglass-flagged user opens
 * a patient chart through the agent. Backs subtask 34.2 of the
 * "Implement Breakglass Audit Logging" Taskmaster task.
 *
 * Authentication is JWT-only: the sidecar forwards the original
 * user-bound JWT (issued by AgentJwtService). The controller validates
 * it with the same secret, then double-checks that the body's user_id
 * and patient_id match the JWT's `sub` and `patient_id` claims —
 * defense-in-depth against a sidecar bug or compromise that might
 * cross-write breakglass rows for one user against another patient.
 *
 * Body validation is parse-don't-validate: missing fields, wrong types,
 * and whitespace-only reasons are rejected with 400 before the writer
 * is touched. Anything that gets to the writer is guaranteed to be a
 * positive int / non-empty trimmed string.
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
use OpenEMR\Modules\AgentForge\Services\BreakglassAuditWriter;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

final class InternalBreakglassController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly BreakglassAuditWriter $writer,
    ) {
    }

    public function handle(Request $request): Response
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
            // Lcobucci's JWT exceptions all implement Lcobucci\JWT\Exception
            // (a marker interface). Catching the umbrella covers
            // InvalidTokenStructure (not-a-JWT), constraint violations,
            // and signature mismatches; RuntimeException covers our own
            // expired-token / claim-shape failures from AgentJwtValidator.
            return new JsonResponse(
                ['error' => 'Invalid or expired token'],
                Response::HTTP_UNAUTHORIZED
            );
        }

        $rawBody = $request->getContent();
        if ($rawBody === '') {
            return new JsonResponse(
                ['error' => 'Request body is required'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $decoded = json_decode($rawBody, true);
        if (!is_array($decoded)) {
            return new JsonResponse(
                ['error' => 'Request body must be a JSON object'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $userId = $decoded['user_id'] ?? null;
        if (!is_int($userId) || $userId <= 0) {
            return new JsonResponse(
                ['error' => 'user_id is required and must be a positive integer'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $patientId = $decoded['patient_id'] ?? null;
        if (!is_int($patientId) || $patientId <= 0) {
            return new JsonResponse(
                ['error' => 'patient_id is required and must be a positive integer'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $reasonRaw = $decoded['reason'] ?? null;
        if (!is_string($reasonRaw)) {
            return new JsonResponse(
                ['error' => 'reason is required and must be a non-empty string'],
                Response::HTTP_BAD_REQUEST
            );
        }
        $reason = trim($reasonRaw);
        if ($reason === '') {
            return new JsonResponse(
                ['error' => 'reason is required and must be a non-empty string'],
                Response::HTTP_BAD_REQUEST
            );
        }

        if ($userId !== $claims->userId) {
            return new JsonResponse(
                ['error' => 'Token sub does not match requested user_id'],
                Response::HTTP_FORBIDDEN
            );
        }

        if ($patientId !== $claims->patientId) {
            return new JsonResponse(
                ['error' => 'Token patient_id does not match requested patient_id'],
                Response::HTTP_FORBIDDEN
            );
        }

        $this->writer->record($userId, $patientId, $reason);

        return new JsonResponse(['logged' => true], Response::HTTP_CREATED);
    }
}
