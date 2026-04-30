<?php

declare(strict_types=1);

/**
 * AgentProxyController — receives browser POSTs to the agent panel,
 * validates the user/patient session context, mints a short-lived JWT,
 * and proxies the request to the Python sidecar.
 *
 * Subtasks 7.1 and 7.2 cover the patient-context + auth guards and the
 * JWT minting integration. Subtask 7.3 fills in the streaming proxy to
 * the sidecar; until then the success path returns a 501 placeholder
 * carrying the freshly minted token.
 *
 * The route URL `/agentforge/turn` is served by `public/turn.php`,
 * which boots OpenEMR and dispatches here. Production deployments may
 * front the module with a reverse-proxy rewrite to expose
 * `/agentforge/turn` at the root rather than under
 * `/interface/modules/custom_modules/oe-module-agentforge/public/turn.php`.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Controllers;

use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use OpenEMR\Modules\AgentForge\Services\BreakglassContext;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class AgentProxyController
{
    public function __construct(
        private readonly AgentJwtService $jwtService,
    ) {
    }

    public function turn(Request $request): Response
    {
        $session = $request->getSession();

        $patientId = $session->get('pid');
        if (!is_int($patientId) || $patientId <= 0) {
            return new JsonResponse(
                [
                    'error' => 'No patient context. Open a patient chart before invoking the agent.',
                ],
                Response::HTTP_BAD_REQUEST
            );
        }

        $userId = $session->get('authUserID');
        $username = $session->get('authUser');
        if (!is_int($userId) || $userId <= 0 || !is_string($username) || $username === '') {
            return new JsonResponse(
                ['error' => 'Authentication required.'],
                Response::HTTP_UNAUTHORIZED
            );
        }

        $body = $this->decodeJsonBody($request);
        $breakglassFlag = $session->get('breakglass_flag', false) === true;
        $breakglassReason = $body['breakglass_reason'] ?? null;
        $breakglass = new BreakglassContext(
            flag: $breakglassFlag,
            reason: is_string($breakglassReason) ? $breakglassReason : null,
        );

        $token = $this->jwtService->mintToken(
            userId: $userId,
            username: $username,
            patientId: $patientId,
            breakglass: $breakglass,
        );

        // Subtask 7.3 will replace this with a StreamedResponse forwarding
        // to the sidecar over HTTP with `Authorization: Bearer <token>`.
        return new JsonResponse(
            [
                '_pending_proxy' => true,
                'token' => $token,
            ],
            Response::HTTP_NOT_IMPLEMENTED
        );
    }

    /**
     * @return array<string, mixed>
     */
    private function decodeJsonBody(Request $request): array
    {
        $content = $request->getContent();
        if ($content === '') {
            return [];
        }
        $decoded = json_decode($content, true);
        if (!is_array($decoded)) {
            return [];
        }
        // Filter to string-keyed entries: json_decode permits int keys
        // but our consumer treats the body as a JSON object.
        $filtered = [];
        foreach ($decoded as $key => $value) {
            if (is_string($key)) {
                $filtered[$key] = $value;
            }
        }
        return $filtered;
    }
}
