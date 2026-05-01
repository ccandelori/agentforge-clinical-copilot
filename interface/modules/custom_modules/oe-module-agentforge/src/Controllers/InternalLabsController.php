<?php

/**
 * InternalLabsController — sidecar-facing read-only endpoint that serves
 * the patient's recent lab analytes for the agent's get_recent_labs tool.
 *
 * Authentication is JWT-only: the sidecar forwards the original user-bound
 * JWT (issued by AgentJwtService) and we validate it with the same secret.
 * The endpoint refuses any pid that doesn't match the JWT's patient_id
 * claim — defense-in-depth against a sidecar bug or compromise that might
 * widen the patient scope of an authenticated request.
 *
 * Unlike the medications/problems/demographics endpoints, this one accepts
 * an optional `since` query parameter (in days) so the model can narrow the
 * lookback window. The bound is server-side clamped (1..365) so a
 * misbehaving model can't request the patient's entire lab history.
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
use OpenEMR\Modules\AgentForge\Services\LabsRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalLabsController
{
    public const DEFAULT_SINCE_DAYS = 90;
    public const MIN_SINCE_DAYS = 1;
    public const MAX_SINCE_DAYS = 365;

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly LabsRepository $repository,
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
            // (a marker interface). Catching that umbrella covers
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

        $since = self::clampSinceDays($request->query->getInt('since_days', self::DEFAULT_SINCE_DAYS));

        $labs = $this->repository->findRecentByPid($pid, $since);
        return new JsonResponse(['labs' => $labs]);
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
