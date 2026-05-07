<?php

/**
 * InternalMeController — sidecar-facing identity-bootstrap endpoint
 * for the dashboard auth bridge (ADR-0001).
 *
 * Resolves an OpenEMR user UUID — typically extracted from an OIDC
 * session's `fhirUser` URI — into the integer user_id + username +
 * primary GACL group that the agent's internal JWT contract requires.
 * The sidecar calls this once per session to bootstrap identity, then
 * mints "real" AGENTFORGE_JWTs for /turn requests using the resolved
 * fields.
 *
 * Authentication: a "lookup-purpose" JWT signed with
 * AGENTFORGE_JWT_SECRET. The validator confirms signature, issuer,
 * and expiration but doesn't require user/patient claims (we don't
 * have them yet — the lookup is what produces them). The trust
 * posture is identical to the other /internal/* endpoints: whoever
 * holds the secret can call it.
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
use OpenEMR\Modules\AgentForge\Services\UserIdentityRepository;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class InternalMeController
{
    public function __construct(
        private readonly AgentJwtValidator $validator,
        private readonly UserIdentityRepository $identities,
        private readonly UserRoleLookup $roles,
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

        $uuid = (string) $request->query->get('user_uuid', '');
        if ($uuid === '') {
            return new JsonResponse(
                ['error' => 'user_uuid query parameter is required'],
                Response::HTTP_BAD_REQUEST,
            );
        }

        $identity = $this->identities->findByUuid($uuid);
        if ($identity === null) {
            return new JsonResponse(
                ['error' => 'No OpenEMR user found for the given UUID'],
                Response::HTTP_NOT_FOUND,
            );
        }

        $role = $this->roles->findPrimaryGroup($identity->username);

        return new JsonResponse([
            'user_id' => $identity->userId,
            'username' => $identity->username,
            'role' => $role,
        ], Response::HTTP_OK);
    }
}
