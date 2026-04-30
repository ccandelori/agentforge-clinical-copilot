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
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;

/**
 * Behavior tests for AgentProxyController.
 *
 * Covers the patient-context guard (subtask 7.1), session validation
 * and JWT minting integration (subtask 7.2). Sidecar proxying lives
 * in 7.3.
 */
final class AgentProxyControllerTest extends TestCase
{
    #[Test]
    public function turnReturns400WhenSessionHasNoPatientId(): void
    {
        $controller = new AgentProxyController(self::createMock(AgentJwtService::class));
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
        $controller = new AgentProxyController(self::createMock(AgentJwtService::class));
        $request = $this->makeRequestWithSession(['pid' => 0]);

        $response = $controller->turn($request);

        self::assertSame(400, $response->getStatusCode());
    }

    #[Test]
    public function turnReturns400WhenSessionPidIsNotAnInt(): void
    {
        $controller = new AgentProxyController(self::createMock(AgentJwtService::class));
        $request = $this->makeRequestWithSession(['pid' => 'definitely-not-a-pid']);

        $response = $controller->turn($request);

        self::assertSame(400, $response->getStatusCode());
    }

    #[Test]
    public function turnReturns401WhenSessionHasNoAuthUserId(): void
    {
        // Patient context present but no authUserID — the user isn't
        // properly logged in. OpenEMR's session middleware should catch
        // this earlier; we still defend against it here.
        $jwtService = self::createMock(AgentJwtService::class);
        $jwtService->expects(self::never())->method('mintToken');

        $controller = new AgentProxyController($jwtService);
        $request = $this->makeRequestWithSession(['pid' => 123, 'authUser' => 'jpatel']);

        $response = $controller->turn($request);

        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function turnReturns401WhenSessionHasNoAuthUsername(): void
    {
        $jwtService = self::createMock(AgentJwtService::class);
        $jwtService->expects(self::never())->method('mintToken');

        $controller = new AgentProxyController($jwtService);
        $request = $this->makeRequestWithSession(['pid' => 123, 'authUserID' => 5]);

        $response = $controller->turn($request);

        self::assertSame(401, $response->getStatusCode());
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
                self::callback(function (BreakglassContext $ctx): bool {
                    return $ctx->flag === false && $ctx->reason === null;
                })
            )
            ->willReturn('fake-jwt-token');

        $controller = new AgentProxyController($jwtService);
        $request = $this->makeRequestWithSession([
            'pid' => 123,
            'authUserID' => 42,
            'authUser' => 'jpatel',
        ]);

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
            ->willReturn('fake-jwt-token');

        $controller = new AgentProxyController($jwtService);
        $request = $this->makeRequestWithSessionAndBody(
            session: [
                'pid' => 123,
                'authUserID' => 42,
                'authUser' => 'jpatel',
                'breakglass_flag' => true,
            ],
            body: ['breakglass_reason' => 'After-hours admit; PCP unreachable.'],
        );

        $controller->turn($request);
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
