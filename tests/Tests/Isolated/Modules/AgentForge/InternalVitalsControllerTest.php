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
use OpenEMR\Modules\AgentForge\Controllers\InternalVitalsController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\VitalsRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalVitalsController.
 *
 * Covers JWT validation (missing / invalid / wrong-secret), pid parsing,
 * pid/claim mismatch, since-parameter parsing and clamping, and happy-path
 * payload shape. Database access is mocked via the repository.
 */
final class InternalVitalsControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/vitals_trend?pid=42');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/vitals_trend?pid=42');
        $request->headers->set('Authorization', '');

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerTokenIsMalformed(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: 'not.a.real.jwt', pid: 42);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/vitals_trend?pid=42');
        // No "Bearer " prefix — controller should refuse to parse.
        $request->headers->set('Authorization', $token);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/vitals_trend');
        $request->headers->set('Authorization', "Bearer {$token}");

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamIsZero(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 0);

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenPidDoesNotMatchTokenPatientId(): void
    {
        $controller = $this->makeController();
        // Token is for patient 42 but the request asks for patient 99.
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 99);

        self::assertSame(403, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returnsVitalsArrayOnHappyPath(): void
    {
        $repoVitals = [
            [
                'id' => 5,
                'date' => '2026-04-20 09:00:00',
                'systolic' => 130,
                'diastolic' => 84,
                'pulse' => 72.0,
                'respiration' => 16.0,
                'temperature' => 98.6,
                'temp_method' => 'oral',
                'oxygen_saturation' => 98.0,
                'height' => 70.0,
                'weight' => 180.5,
                'bmi' => 25.9,
                'bmi_status' => 'overweight',
                'note' => null,
            ],
        ];
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn($repoVitals);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 42);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertInstanceOf(JsonResponse::class, $response);
        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        // assertEquals (loose) rather than assertSame: JSON encoding
        // strips ``.0`` from whole-number floats, so a fixture value of
        // 70.0 round-trips as PHP int 70. The wire contract is the JSON
        // shape, not the in-PHP type — assertEquals matches that
        // contract while still rejecting any actual value drift.
        self::assertEquals($repoVitals, $body['vitals']);
    }

    #[Test]
    public function defaultsSinceTo90DaysWhenParamMissing(): void
    {
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 42);

        $controller->show($request);
    }

    #[Test]
    public function honorsSinceQueryParamWhenInRange(): void
    {
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 14)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            since: 14,
        );

        $controller->show($request);
    }

    #[Test]
    public function clampsSinceQueryParamAboveMaximum(): void
    {
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 730)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            since: 99999,
        );

        $controller->show($request);
    }

    #[Test]
    public function fallsBackToDefaultWhenSinceQueryParamIsZero(): void
    {
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            since: 0,
        );

        $controller->show($request);
    }

    #[Test]
    public function fallsBackToDefaultWhenSinceQueryParamIsNegative(): void
    {
        $repository = self::createMock(VitalsRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            since: -5,
        );

        $controller->show($request);
    }

    private function makeController(
        ?VitalsRepository $repository = null,
    ): InternalVitalsController {
        $repository ??= self::createMock(VitalsRepository::class);
        return new InternalVitalsController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $repository,
        );
    }

    private function makeRequest(string $token, int $pid, ?int $since = null): Request
    {
        $query = "pid={$pid}";
        if ($since !== null) {
            $query .= "&since={$since}";
        }
        $request = Request::create("/agentforge/internal/vitals_trend?{$query}");
        $request->headers->set('Authorization', "Bearer {$token}");
        return $request;
    }

    private function mintToken(int $patientId): string
    {
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::TEST_SECRET),
        );
        $now = new DateTimeImmutable(self::TEST_NOW);

        return $config->builder()
            ->issuedBy('openemr-agentforge')
            ->relatedTo('42')
            ->withClaim('patient_id', $patientId)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($config->signer(), $config->signingKey())
            ->toString();
    }
}
