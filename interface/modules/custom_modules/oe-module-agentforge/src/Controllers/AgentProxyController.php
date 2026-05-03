<?php

declare(strict_types=1);

/**
 * AgentProxyController — receives browser POSTs to the agent panel,
 * validates the user/patient session context, mints a short-lived JWT,
 * and proxies the request to the Python sidecar.
 *
 * The route URL `/agentforge/turn` is served by `public/turn.php`,
 * which boots OpenEMR and dispatches here. Production deployments may
 * front the module with a reverse-proxy rewrite to expose
 * `/agentforge/turn` at the root rather than under
 * `/interface/modules/custom_modules/oe-module-agentforge/public/turn.php`.
 *
 * Failure modes are mapped explicitly to HTTP status codes so the
 * browser-side panel can act on them:
 *   400  no patient context (open a chart first)
 *   401  no auth (user not logged in)
 *   502  sidecar returned non-2xx (orchestrator/agent error)
 *   503  sidecar transport failure (unreachable / timeout)
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
use Symfony\Component\HttpFoundation\StreamedResponse;
use Symfony\Contracts\HttpClient\Exception\ExceptionInterface as HttpClientExceptionInterface;
use Symfony\Contracts\HttpClient\Exception\TransportExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;
use Symfony\Contracts\HttpClient\ResponseInterface;

class AgentProxyController
{
    public function __construct(
        private readonly AgentJwtService $jwtService,
        private readonly HttpClientInterface $httpClient,
        private readonly string $sidecarBaseUrl,
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

        try {
            $sidecarResponse = $this->httpClient->request(
                'POST',
                rtrim($this->sidecarBaseUrl, '/') . '/turn',
                [
                    'headers' => [
                        'Authorization' => 'Bearer ' . $token,
                        'Content-Type' => 'application/json',
                    ],
                    'body' => $request->getContent(),
                ]
            );
            $statusCode = $sidecarResponse->getStatusCode();
        } catch (TransportExceptionInterface $e) {
            return new JsonResponse(
                [
                    'error' => 'Agent sidecar unreachable. Please retry shortly.',
                    'detail' => $e->getMessage(),
                ],
                Response::HTTP_SERVICE_UNAVAILABLE
            );
        } catch (HttpClientExceptionInterface $e) {
            return new JsonResponse(
                ['error' => 'Agent sidecar request failed.'],
                Response::HTTP_BAD_GATEWAY
            );
        }

        if ($statusCode < 200 || $statusCode >= 300) {
            return new JsonResponse(
                [
                    'error' => 'Agent sidecar returned an error.',
                    'sidecar_status' => $statusCode,
                ],
                Response::HTTP_BAD_GATEWAY
            );
        }

        return $this->streamSidecarResponse($sidecarResponse, $userId, $patientId);
    }

    /**
     * Build a StreamedResponse that pipes the sidecar's response body
     * through to the client without buffering the full body. The
     * httpClient is captured by reference inside the streaming callback
     * so streaming continues across the closure boundary.
     *
     * When the sidecar returns ``text/event-stream`` (SSE streaming path,
     * week1-gaps Task #11), two extra headers suppress proxy and PHP-layer
     * buffering so each SSE frame reaches the browser as it is emitted:
     *   - ``Cache-Control: no-cache`` — prevents the browser / CDN from
     *     buffering the SSE stream across reconnects.
     *   - ``X-Accel-Buffering: no`` — nginx-specific knob; disables proxy
     *     buffering so deltas are not held until the connection closes.
     *
     * Cost is carried inside the SSE ``final`` frame emitted by the sidecar
     * (see ``main.py:_sse_stream``); the JS reader extracts it from there.
     * No attempt is made to re-emit it as an HTTP header because response
     * headers cannot be set after the stream body has started.
     *
     * ``X-Trace-Id`` is forwarded from the sidecar when Langfuse is
     * configured so operators can correlate an HTTP request to a Langfuse
     * trace. It is also logged via ``error_log`` alongside the user and
     * patient IDs to support log-based correlation queries.
     */
    private function streamSidecarResponse(
        ResponseInterface $sidecarResponse,
        int $userId,
        int $patientId,
    ): StreamedResponse {
        $client = $this->httpClient;

        // Inspect headers before opening the streaming callback — once the
        // callback starts writing, headers are already sent.
        $sidecarHeaders = $sidecarResponse->getHeaders(throw: false);
        $contentType = $sidecarHeaders['content-type'][0] ?? '';
        $isSse = str_starts_with($contentType, 'text/event-stream');
        $traceId = $sidecarHeaders['x-trace-id'][0] ?? '';

        // Log the trace correlation record so operators can join HTTP
        // access logs to Langfuse traces without browser-side tooling.
        if ($traceId !== '') {
            error_log(sprintf(
                'agentforge trace_id=%s user_id=%d patient_id=%d',
                $traceId,
                $userId,
                $patientId,
            ));
        }

        $streamed = new StreamedResponse(function () use ($client, $sidecarResponse): void {
            foreach ($client->stream($sidecarResponse) as $chunk) {
                echo $chunk->getContent();
                if (function_exists('flush')) {
                    flush();
                }
            }
        }, Response::HTTP_OK);

        // Forward Content-Type from sidecar so JSON / SSE / plain text
        // all reach the browser correctly.
        if ($contentType !== '') {
            $streamed->headers->set('Content-Type', $contentType);
        }

        // Forward X-Trace-Id so the browser-side panel can surface the
        // trace link for debugging without a server-log lookup.
        if ($traceId !== '') {
            $streamed->headers->set('X-Trace-Id', $traceId);
        }

        if ($isSse) {
            $streamed->headers->set('Cache-Control', 'no-cache');
            $streamed->headers->set('X-Accel-Buffering', 'no');
        }

        return $streamed;
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
        $filtered = [];
        foreach ($decoded as $key => $value) {
            if (is_string($key)) {
                $filtered[$key] = $value;
            }
        }
        return $filtered;
    }
}
