<?php

declare(strict_types=1);

/**
 * AgentProxyController — receives browser POSTs to the agent panel,
 * validates the user/patient session context, mints a short-lived JWT,
 * and proxies the request to the Python sidecar.
 *
 * Subtask 7.1 covers the "no patient context" guard. Subtasks 7.2 and
 * 7.3 fill in JWT minting and the streaming proxy to the sidecar.
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

use LogicException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

class AgentProxyController
{
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

        // Subtasks 7.2 and 7.3 fill in:
        //   - read user/breakglass context from the session and request body
        //   - mint the JWT via AgentJwtService
        //   - forward to the sidecar over HTTP and stream the response back
        throw new LogicException(
            'AgentProxyController::turn full implementation lands in subtasks 7.2 and 7.3. '
            . 'Currently only the no-patient-context guard is wired up.'
        );
    }
}
