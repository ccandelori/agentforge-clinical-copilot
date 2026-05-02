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
use OpenEMR\Modules\AgentForge\Controllers\InternalNotesController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\NotesRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalNotesController.
 *
 * Same shape as InternalLabsControllerTest / InternalVitalsControllerTest:
 * JWT validation (missing / invalid / wrong-secret / not-a-jwt), pid
 * parsing, pid/claim mismatch, since-parameter parsing and clamping, and
 * happy-path payload shape. Database access is mocked via the repository.
 */
final class InternalNotesControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/recent_notes?pid=42');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/recent_notes?pid=42');
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
        $request = Request::create('/agentforge/internal/recent_notes?pid=42');
        $request->headers->set('Authorization', $token);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/recent_notes');
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
    public function returnsNotesArrayOnHappyPath(): void
    {
        $repoNotes = [
            [
                'id' => 5,
                'source' => 'pnote',
                'date' => '2026-04-20 14:30:00',
                'author' => 'dr.smith',
                'title' => 'Phone call',
                'body' => 'Patient reports improvement.',
                'note_type' => null,
            ],
        ];
        $repository = self::createMock(NotesRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 90)
            ->willReturn($repoNotes);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), pid: 42);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertInstanceOf(JsonResponse::class, $response);
        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        self::assertSame($repoNotes, $body['notes']);
    }

    #[Test]
    public function defaultsSinceTo90DaysWhenParamMissing(): void
    {
        $repository = self::createMock(NotesRepository::class);
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
        $repository = self::createMock(NotesRepository::class);
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
        $repository = self::createMock(NotesRepository::class);
        $repository
            ->expects(self::once())
            ->method('findRecentByPid')
            ->with(42, 365)
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
        $repository = self::createMock(NotesRepository::class);
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
        $repository = self::createMock(NotesRepository::class);
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
        ?NotesRepository $repository = null,
    ): InternalNotesController {
        $repository ??= self::createMock(NotesRepository::class);
        return new InternalNotesController(
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
        $request = Request::create("/agentforge/internal/recent_notes?{$query}");
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
