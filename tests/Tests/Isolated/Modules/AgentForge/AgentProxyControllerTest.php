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
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;

/**
 * Behavior tests for AgentProxyController.
 *
 * Subtask 7.1 covers the controller's "no patient context" guard:
 * any request that arrives without a patient id in the session must
 * be refused with a 400 response. Subsequent subtasks (7.2 / 7.3)
 * layer in JWT minting and proxying to the sidecar.
 */
final class AgentProxyControllerTest extends TestCase
{
    #[Test]
    public function turnReturns400WhenSessionHasNoPatientId(): void
    {
        $controller = new AgentProxyController();
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
        // pid=0 is OpenEMR's sentinel for "no patient selected" in some
        // legacy flows; it must be treated identically to a missing pid.
        $controller = new AgentProxyController();
        $request = $this->makeRequestWithSession(['pid' => 0]);

        $response = $controller->turn($request);

        self::assertSame(400, $response->getStatusCode());
    }

    #[Test]
    public function turnReturns400WhenSessionPidIsNotAnInt(): void
    {
        // Defensive: a session value that's somehow a string or array
        // shouldn't fall through to the JWT minter (which expects int).
        $controller = new AgentProxyController();
        $request = $this->makeRequestWithSession(['pid' => 'definitely-not-a-pid']);

        $response = $controller->turn($request);

        self::assertSame(400, $response->getStatusCode());
    }

    /**
     * @param array<string, mixed> $sessionData
     */
    private function makeRequestWithSession(array $sessionData): Request
    {
        $session = new Session(new MockArraySessionStorage());
        foreach ($sessionData as $key => $value) {
            $session->set($key, $value);
        }

        $request = Request::create('/agentforge/turn', 'POST');
        $request->setSession($session);

        return $request;
    }
}
