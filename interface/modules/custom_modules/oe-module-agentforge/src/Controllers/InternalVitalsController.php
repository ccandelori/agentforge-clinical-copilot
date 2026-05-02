<?php

/**
 * InternalVitalsController — sidecar-facing read-only endpoint that
 * serves the patient's recent vital sign measurements for the agent's
 * get_vitals_trend tool.
 *
 * Authentication is JWT-only: the sidecar forwards the original user-bound
 * JWT (issued by AgentJwtService) and we validate it with the same secret.
 * The endpoint refuses any pid that doesn't match the JWT's patient_id
 * claim — defense-in-depth against a sidecar bug or compromise that might
 * widen the patient scope of an authenticated request.
 *
 * Accepts an optional `since` query parameter (integer days) controlling
 * the lookback window. Default 90, clamped to [1, 730]. Final clamping
 * lives in the repository so the controller stays a thin parser.
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
use OpenEMR\Modules\AgentForge\Services\VitalsRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalVitalsController
{
    private const DEFAULT_SINCE_DAYS = 90;
    private const MAX_SINCE_DAYS = 730;

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly VitalsRepository $repository,
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

        $since = $request->query->getInt('since', self::DEFAULT_SINCE_DAYS);
        if ($since <= 0) {
            $since = self::DEFAULT_SINCE_DAYS;
        }
        if ($since > self::MAX_SINCE_DAYS) {
            $since = self::MAX_SINCE_DAYS;
        }

        $vitals = $this->repository->findRecentByPid($pid, $since);
        return new JsonResponse(['vitals' => $vitals]);
    }
}
