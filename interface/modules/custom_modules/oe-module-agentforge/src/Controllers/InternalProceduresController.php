<?php

/**
 * InternalProceduresController — sidecar-facing read-only endpoint that
 * serves the patient's recent procedure history for the agent's
 * get_procedures tool.
 *
 * Authentication is JWT-only: the sidecar forwards the original
 * user-bound JWT (issued by AgentJwtService) and we validate it with
 * the same secret. The endpoint refuses any pid that doesn't match the
 * JWT's patient_id claim — defense-in-depth against a sidecar bug or
 * compromise that might widen the patient scope of an authenticated
 * request.
 *
 * Like the labs/vitals endpoints, this one accepts an optional
 * `since_days` query parameter (default 365, server-clamped to
 * 1..1825) so the model can narrow or widen the lookback window.
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
use OpenEMR\Modules\AgentForge\Services\ProceduresRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalProceduresController
{
    public const DEFAULT_SINCE_DAYS = 365;
    public const MIN_SINCE_DAYS = 1;
    // 5 years — covers most surgical-history questions without letting
    // a misbehaving model request the patient's entire chart.
    public const MAX_SINCE_DAYS = 1825;

    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly ProceduresRepository $repository,
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

        $since = self::clampSinceDays(
            $request->query->getInt('since_days', self::DEFAULT_SINCE_DAYS)
        );

        $procedures = $this->repository->findRecentByPid($pid, $since);
        return new JsonResponse(['procedures' => $procedures]);
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
