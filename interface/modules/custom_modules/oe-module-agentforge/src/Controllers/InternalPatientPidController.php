<?php

/**
 * InternalPatientPidController — sidecar-facing patient-bootstrap
 * endpoint. Resolves a FHIR Patient resource UUID into the integer
 * ``patient_data.pid`` the agent's internal JWT carries.
 *
 * Pairs with InternalMeController: the dashboard auth bridge
 * (ADR-0001) needs both lookups to mint a complete internal JWT
 * (user_id from /me, patient_id from this endpoint) before
 * Orchestrator.turn() can run.
 *
 * Auth: a "lookup-purpose" JWT signed with AGENTFORGE_JWT_SECRET.
 * Same trust posture as /me — whoever holds the secret can call.
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
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalPatientPidController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly PatientPidRepository $repository,
    ) {
    }

    public function show(Request $request): Response
    {
        $authHeader = $request->headers->get('Authorization');
        if ($authHeader === null || $authHeader === '') {
            return new JsonResponse(
                ['error' => 'Authorization header is required'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        try {
            $this->validator->validateLookupBearer($authHeader);
        } catch (JwtException | RuntimeException $e) {
            return new JsonResponse(
                ['error' => 'Invalid or expired token'],
                Response::HTTP_UNAUTHORIZED,
            );
        }

        $uuid = (string) $request->query->get('patient_uuid', '');
        if ($uuid === '') {
            return new JsonResponse(
                ['error' => 'patient_uuid query parameter is required'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $pid = $this->repository->findPidByUuid($uuid);
        if ($pid === null) {
            return new JsonResponse(
                ['error' => 'No OpenEMR patient found for the given UUID'],
                Response::HTTP_NOT_FOUND,
            );
        }

        return new JsonResponse(['pid' => $pid], Response::HTTP_OK);
    }
}
