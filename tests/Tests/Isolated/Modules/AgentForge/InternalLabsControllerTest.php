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

use DateTimeImmutable;
use Lcobucci\Clock\FrozenClock;
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use OpenEMR\Modules\AgentForge\Controllers\InternalLabsController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\LabsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalLabsController.
 *
 * Treats the controller as the trust boundary between the sidecar and
 * the database. Validates JWT/pid coupling, since_days clamping, and
 * the response shape. Repository is mocked; tokens are minted with a
 * real lcobucci builder so signature verification flows like prod.
 */
final class InternalLabsControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';  // 32 bytes
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function returns401WhenAuthorizationHeaderIsMissing(): void
    {
        $controller = $this->makeController(repository: $this->stubRepository());
        $request = Request::create('/labs?pid=42');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenTokenIsInvalid(): void
    {
        $controller = $this->makeController(repository: $this->stubRepository());
        $request = Request::create('/labs?pid=42');
        $request->headers->set('Authorization', 'Bearer not-a-real-token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidIsMissing(): void
    {
        $controller = $this->makeController(repository: $this->stubRepository());
        $request = Request::create('/labs');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_BAD_REQUEST, $response->getStatusCode());
    }

    #[Test]
    public function returns403WhenTokenPidDoesNotMatchRequestedPid(): void
    {
        $controller = $this->makeController(repository: $this->stubRepository());
        $request = Request::create('/labs?pid=999');
        // Token says patient_id=42; URL says pid=999.
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_FORBIDDEN, $response->getStatusCode());
    }

    #[Test]
    public function returnsLabsArrayInJsonResponseOnSuccess(): void
    {
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([
                [
                    'id' => 1,
                    'order_id' => 10,
                    'report_id' => 20,
                    'test_code' => '2160-0',
                    'test_name' => 'Creatinine',
                    'value' => '1.1',
                    'units' => 'mg/dL',
                    'reference_range' => '0.6 - 1.2',
                    'abnormal' => 'no',
                    'date' => '2026-04-15',
                ],
            ]);

        $controller = $this->makeController(repository: $repo);
        $request = Request::create('/labs?pid=42');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());
        $body = $this->decodeJson($response);
        self::assertCount(1, $body['labs']);
        self::assertSame('Creatinine', $body['labs'][0]['test_name']);
    }

    #[Test]
    public function defaultsTo90DaysWhenSinceParamMissing(): void
    {
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repo);
        $request = Request::create('/labs?pid=42');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $controller->show($request);
    }

    #[Test]
    public function passesExplicitSinceDaysThrough(): void
    {
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 30)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repo);
        $request = Request::create('/labs?pid=42&since_days=30');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $controller->show($request);
    }

    #[Test]
    public function clampsSinceDaysAtUpperBoundOf365(): void
    {
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repo);
        $request = Request::create('/labs?pid=42&since_days=99999');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $controller->show($request);
    }

    #[Test]
    public function fallsBackToDefaultWhenSinceDaysIsNonPositive(): void
    {
        // Negative or zero is meaningless — fall back to default rather
        // than refuse the request, so a model emitting `since_days: 0`
        // still gets useful data.
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repo);
        $request = Request::create('/labs?pid=42&since_days=0');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $controller->show($request);
    }

    private function makeController(LabsRepository $repository): InternalLabsController
    {
        return new InternalLabsController(
            new AgentJwtValidator(self::TEST_SECRET, $this->makeClock()),
            $repository,
        );
    }

    private function stubRepository(): LabsRepository
    {
        // For tests that 4xx out before the repository is used; any
        // findRecentByPid call would be a test bug.
        $repo = $this->createMock(LabsRepository::class);
        $repo->expects(self::never())->method('findRecentByPid');
        return $repo;
    }

    private function makeClock(): ClockInterface
    {
        return new FrozenClock(new DateTimeImmutable(self::TEST_NOW));
    }

    private function mintToken(int $patientId, int $userId = 7): string
    {
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::TEST_SECRET),
        );
        $now = new DateTimeImmutable(self::TEST_NOW);

        return $config->builder()
            ->issuedBy('openemr-agentforge')
            ->relatedTo((string) $userId)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->withClaim('patient_id', $patientId)
            ->withClaim('username', 'jpatel')
            ->withClaim('role', 'Physicians')
            ->withClaim('breakglass_flag', false)
            ->withClaim('breakglass_reason', null)
            ->getToken($config->signer(), $config->signingKey())
            ->toString();
    }

    /**
     * @return array<string, mixed>
     */
    private function decodeJson(Response $response): array
    {
        $content = $response->getContent();
        self::assertIsString($content);
        $decoded = json_decode($content, true);
        self::assertIsArray($decoded);
        /** @var array<string, mixed> $decoded */
        return $decoded;
    }
}
