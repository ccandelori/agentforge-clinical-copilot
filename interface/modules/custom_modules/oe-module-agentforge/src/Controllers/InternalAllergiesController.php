<?php

declare(strict_types=1);

/**
 * InternalAllergiesController — sidecar-facing read-only endpoint that
 * serves the patient's active allergies for the agent's
 * get_active_allergies tool.
 *
 * Authentication is JWT-only: the sidecar forwards the original user-bound
 * JWT (issued by AgentJwtService) and we validate it with the same secret.
 * The endpoint refuses any pid that doesn't match the JWT's patient_id
 * claim — defense-in-depth against a sidecar bug or compromise that might
 * widen the patient scope of an authenticated request.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Controllers;

use Lcobucci\JWT\Validation\RequiredConstraintsViolated;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\AllergiesRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalAllergiesController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly AllergiesRepository $repository,
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
        } catch (RequiredConstraintsViolated | RuntimeException $e) {
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

        $allergies = $this->repository->findActiveByPid($pid);
        return new JsonResponse(['allergies' => $allergies]);
    }
}
