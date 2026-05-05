<?php

/**
 * InternalNotesSearchController — sidecar-facing read-only endpoint that
 * runs a patient-scoped FULLTEXT search across pnotes + form_clinical_notes
 * for the agent's search_notes tool.
 *
 * Authentication is JWT-only: the sidecar forwards the original user-bound
 * JWT (issued by AgentJwtService) and we validate it with the same secret.
 * The endpoint refuses any pid that doesn't match the JWT's patient_id
 * claim — defense-in-depth against a sidecar bug or compromise that might
 * widen the patient scope of an authenticated request.
 *
 * Query params:
 *   - pid (int, required): must equal the JWT patient_id claim.
 *   - q (string, required): the search phrase. Trimmed; empty/whitespace
 *     after trim is rejected with 400.
 *   - limit (int, optional, default 5): clamped server-side to [1, 10].
 *     0 / negative falls back to the default.
 *   - since_days (int, optional, default 365): clamped to [1, 365] —
 *     intentionally broader than recent_notes because users may search
 *     deep into a patient's history.
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
use OpenEMR\Modules\AgentForge\Services\NotesSearchRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

final class InternalNotesSearchController
{
    public const DEFAULT_LIMIT = 5;
    public const MIN_LIMIT = 1;
    public const MAX_LIMIT = 10;
    public const DEFAULT_SINCE_DAYS = 365;
    public const MIN_SINCE_DAYS = 1;
    public const MAX_SINCE_DAYS = 365;

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly NotesSearchRepository $repository,
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

        $pid = $request->query->getInt('pid', 0);
        if ($pid <= 0) {
            return new JsonResponse(
                ['error' => 'pid query parameter is required and must be positive'],
                Response::HTTP_BAD_REQUEST
            );
        }

        if ($pid !== $claims->patientId) {
            return new JsonResponse(
                ['error' => 'Token patient_id does not match requested pid'],
                Response::HTTP_FORBIDDEN
            );
        }

        // getString() narrows the InputBag value to string with no extra
        // is_string() check — PHPStan's symfony extension already proves
        // it. trim() then strips intake whitespace before validation.
        $query = trim($request->query->getString('q', ''));
        if ($query === '') {
            return new JsonResponse(
                ['error' => 'q query parameter is required and must be non-empty'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $limit = self::clampLimit(
            $request->query->getInt('limit', self::DEFAULT_LIMIT)
        );
        $since = self::clampSinceDays(
            $request->query->getInt('since_days', self::DEFAULT_SINCE_DAYS)
        );

        $results = $this->repository->search($pid, $query, $limit, $since);
        return new JsonResponse(['results' => $results]);
    }

    private static function clampLimit(int $raw): int
    {
        if ($raw < self::MIN_LIMIT) {
            return self::DEFAULT_LIMIT;
        }
        if ($raw > self::MAX_LIMIT) {
            return self::MAX_LIMIT;
        }
        return $raw;
    }

    private static function clampSinceDays(int $raw): int
    {
        if ($raw < self::MIN_SINCE_DAYS) {
            return self::DEFAULT_SINCE_DAYS;
        }
        if ($raw > self::MAX_SINCE_DAYS) {
            return self::MAX_SINCE_DAYS;
        }
        return $raw;
    }
}
