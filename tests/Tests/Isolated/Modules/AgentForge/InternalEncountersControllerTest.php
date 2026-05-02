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
use OpenEMR\Modules\AgentForge\Controllers\InternalEncountersController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\EncountersRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalEncountersController.
 *
 * Same shape as InternalNotesControllerTest: JWT validation (missing /
 * empty / malformed / wrong-scheme), pid parsing, pid/claim mismatch,
 * since-parameter parsing and clamping, and happy-path payload shape.
 * Database access is mocked via the repository.
 */
final class InternalEncountersControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/recent_encounters?pid=42');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/recent_encounters?pid=42');
        $request->headers->set('Authorization', '');

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerTokenIsMalformed(): void
    {
        // Catches the InvalidTokenStructure backport: a bearer that isn't a
        // JWT shape at all should land as 401, not 500. Without the
        // umbrella JwtException catch this falls through to a 500.
        $controller = $this->makeController();
        $request = $this->makeRequest(token: 'not.a.real.jwt', pid: 42);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/recent_encounters?pid=42');
        $request->headers->set('Authorization', $token);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/recent_encounters');
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
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 99);

        self::assertSame(403, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returnsEncountersArrayOnHappyPath(): void
    {
        $repoEncounters = [
            [
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'reason' => 'follow-up for diabetes',
                'encounter_type' => 'Office Visit',
                'class_code' => 'AMB',
                'provider_id' => 12,
                'provider_name' => 'dr.smith',
                'sensitivity' => null,
                'encounter_category' => 5,
            ],
        ];
        $repository = self::createMock(EncountersRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
            ->willReturn($repoEncounters);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 42);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertInstanceOf(JsonResponse::class, $response);
        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        self::assertSame($repoEncounters, $body['encounters']);
    }

    #[Test]
    public function defaultsSinceTo365DaysWhenParamMissing(): void
    {
        $repository = self::createMock(EncountersRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 42);

        $controller->show($request);
    }

    #[Test]
    public function honorsSinceQueryParamWhenInRange(): void
    {
        $repository = self::createMock(EncountersRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 60)
            ->willReturn([]);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            since: 60,
        );

        $controller->show($request);
    }

    #[Test]
    public function clampsSinceQueryParamAboveMaximum(): void
    {
        $repository = self::createMock(EncountersRepository::class);
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
        $repository = self::createMock(EncountersRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
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
        $repository = self::createMock(EncountersRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
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
        ?EncountersRepository $repository = null,
    ): InternalEncountersController {
        $repository ??= self::createMock(EncountersRepository::class);
        return new InternalEncountersController(
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
            $query .= "&since_days={$since}";
        }
        $request = Request::create("/agentforge/internal/recent_encounters?{$query}");
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
