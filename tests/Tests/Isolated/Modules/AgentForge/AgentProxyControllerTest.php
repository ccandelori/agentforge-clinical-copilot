<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\AgentForge;

use OpenEMR\Modules\AgentForge\Controllers\AgentProxyController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use OpenEMR\Modules\AgentForge\Services\BreakglassContext;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpClient\Exception\TransportException;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Symfony\Contracts\HttpClient\HttpClientInterface;

/**
 * Behavior tests for AgentProxyController.
 *
 * Covers the patient-context guard (subtask 7.1), session validation
 * and JWT minting integration (subtask 7.2), and the streaming sidecar
 * proxy with error handling (subtask 7.3).
 */
final class AgentProxyControllerTest extends TestCase
{
    private const SIDECAR_BASE_URL = 'http://sidecar:8000';

    #[Test]
    public function turnReturns400WhenSessionHasNoPatientId(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequestWithSession([]);

        $response = $controller->turn($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(400, $response->getStatusCode());
        self::assertStringContainsString(
            'patient',
            (string) $response->getContent()
        );
    }

    #[Test]
    public function turnReturns400WhenSessionPidIsZero(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequestWithSession(['pid' => 0]);

        self::assertSame(400, $controller->turn($request)->getStatusCode());
    }

    #[Test]
    public function turnReturns400WhenSessionPidIsNotAnInt(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequestWithSession(['pid' => 'definitely-not-a-pid']);

        self::assertSame(400, $controller->turn($request)->getStatusCode());
    }

    #[Test]
    public function turnReturns401WhenSessionHasNoAuthUserId(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequestWithSession(['pid' => 123, 'authUser' => 'jpatel']);

        self::assertSame(401, $controller->turn($request)->getStatusCode());
    }

    #[Test]
    public function turnReturns401WhenSessionHasNoAuthUsername(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequestWithSession(['pid' => 123, 'authUserID' => 5]);

        self::assertSame(401, $controller->turn($request)->getStatusCode());
    }

    #[Test]
    public function turnMintsJwtWithSessionUserAndBreakglassInactiveByDefault(): void
    {
        $jwtService = self::createMock(AgentJwtService::class);
        $jwtService->expects(self::once())
            ->method('mintToken')
            ->with(
                42,
                'jpatel',
                123,
                self::callback(
                    fn (BreakglassContext $ctx): bool =>
                        $ctx->flag === false && $ctx->reason === null
                )
            )
            ->willReturn('fake.jwt.token');

        $controller = $this->makeController(jwtService: $jwtService);
        $request = $this->makeAuthenticatedRequest();

        $controller->turn($request);
    }

    #[Test]
    public function turnPropagatesBreakglassFlagAndReasonIntoMintedToken(): void
    {
        $jwtService = self::createMock(AgentJwtService::class);
        $jwtService->expects(self::once())
            ->method('mintToken')
            ->with(
                42,
                'jpatel',
                123,
                self::callback(
                    fn (BreakglassContext $ctx): bool =>
                        $ctx->flag === true
                        && $ctx->reason === 'After-hours admit; PCP unreachable.'
                )
            )
            ->willReturn('fake.jwt.token');

        $controller = $this->makeController(jwtService: $jwtService);
        $request = $this->makeAuthenticatedRequest(
            extraSession: ['breakglass_flag' => true],
            body: ['breakglass_reason' => 'After-hours admit; PCP unreachable.'],
        );

        $controller->turn($request);
    }

    #[Test]
    public function turnForwardsBodyAndJwtBearerHeaderToSidecar(): void
    {
        $captured = [];
        $httpClient = new MockHttpClient(function (
            string $method,
            string $url,
            array $options
        ) use (&$captured): MockResponse {
            $captured = ['method' => $method, 'url' => $url, 'options' => $options];
            return new MockResponse('{"reply":"ok"}', ['http_code' => 200]);
        });

        $controller = $this->makeController(httpClient: $httpClient);
        $request = $this->makeAuthenticatedRequest(
            body: ['message' => 'summarize this patient'],
        );

        $controller->turn($request);

        self::assertSame('POST', $captured['method']);
        self::assertSame(self::SIDECAR_BASE_URL . '/turn', $captured['url']);

        // Headers in MockHttpClient come back via the 'headers' option,
        // already normalised to "Header: value" lines.
        $options = $captured['options'];
        self::assertIsArray($options);
        $headers = $options['headers'];
        self::assertIsArray($headers);
        self::assertContains('Authorization: Bearer fake.jwt.token', $headers);
        self::assertContains('Content-Type: application/json', $headers);

        $body = $options['body'];
        self::assertIsString($body);
        self::assertStringContainsString('summarize this patient', $body);
    }

    #[Test]
    public function turnStreamsSidecarTwoHundredResponseToClient(): void
    {
        $sidecarBody = '{"reply":"Patient is stable."}';
        $httpClient = new MockHttpClient(
            new MockResponse($sidecarBody, ['http_code' => 200])
        );

        $controller = $this->makeController(httpClient: $httpClient);
        $request = $this->makeAuthenticatedRequest();

        $response = $controller->turn($request);

        self::assertInstanceOf(StreamedResponse::class, $response);
        self::assertSame(200, $response->getStatusCode());

        ob_start();
        $response->sendContent();
        $output = (string) ob_get_clean();
        self::assertSame($sidecarBody, $output);
    }

    #[Test]
    public function turnReturns502WhenSidecarReturns5xx(): void
    {
        $httpClient = new MockHttpClient(
            new MockResponse('{"error":"orchestrator crashed"}', ['http_code' => 500])
        );

        $controller = $this->makeController(httpClient: $httpClient);
        $request = $this->makeAuthenticatedRequest();

        $response = $controller->turn($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(502, $response->getStatusCode());
    }

    #[Test]
    public function turnReturns503WhenSidecarTransportFails(): void
    {
        $httpClient = new MockHttpClient(function (): never {
            throw new TransportException('Connection refused');
        });

        $controller = $this->makeController(httpClient: $httpClient);
        $request = $this->makeAuthenticatedRequest();

        $response = $controller->turn($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(503, $response->getStatusCode());
    }

    private function makeController(
        ?AgentJwtService $jwtService = null,
        ?HttpClientInterface $httpClient = null,
    ): AgentProxyController {
        if ($jwtService === null) {
            $jwtService = self::createMock(AgentJwtService::class);
            $jwtService->method('mintToken')->willReturn('fake.jwt.token');
        }
        $httpClient ??= new MockHttpClient(new MockResponse('{}', ['http_code' => 200]));

        return new AgentProxyController(
            jwtService: $jwtService,
            httpClient: $httpClient,
            sidecarBaseUrl: self::SIDECAR_BASE_URL,
        );
    }

    /**
     * @param array<string, mixed> $extraSession
     * @param array<string, mixed> $body
     */
    private function makeAuthenticatedRequest(
        array $extraSession = [],
        array $body = [],
    ): Request {
        return $this->makeRequestWithSessionAndBody(
            session: array_merge(
                ['pid' => 123, 'authUserID' => 42, 'authUser' => 'jpatel'],
                $extraSession,
            ),
            body: $body,
        );
    }

    /**
     * @param array<string, mixed> $sessionData
     */
    private function makeRequestWithSession(array $sessionData): Request
    {
        return $this->makeRequestWithSessionAndBody($sessionData, []);
    }

    /**
     * @param array<string, mixed> $session
     * @param array<string, mixed> $body
     */
    private function makeRequestWithSessionAndBody(array $session, array $body): Request
    {
        $sym = new Session(new MockArraySessionStorage());
        foreach ($session as $key => $value) {
            $sym->set($key, $value);
        }

        $jsonBody = $body === [] ? '' : (string) json_encode($body);
        $request = Request::create('/agentforge/turn', 'POST', [], [], [], [], $jsonBody);
        $request->headers->set('Content-Type', 'application/json');
        $request->setSession($sym);

        return $request;
    }
}
