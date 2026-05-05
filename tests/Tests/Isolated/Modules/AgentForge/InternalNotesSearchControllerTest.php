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
use Doctrine\DBAL\Connection;
use Lcobucci\Clock\FrozenClock;
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use OpenEMR\Modules\AgentForge\Controllers\InternalNotesSearchController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\NotesSearchRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalNotesSearchController.
 *
 * Same shape as InternalNotesControllerTest: JWT validation (missing /
 * invalid / malformed / wrong-secret), pid parsing, pid/claim mismatch,
 * `q` parsing (must be present + non-empty after trim), limit/since_days
 * parsing and clamping, and happy-path payload shape.
 *
 * Because NotesSearchRepository is final (per modern conventions), the
 * controller tests inject a real repository wired to a mocked Connection.
 * Captured SQL/params at the Connection layer are how we verify the
 * controller forwarded clamped pid / q / limit / since values correctly.
 */
final class InternalNotesSearchControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/notes_search?pid=42&q=cough');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/notes_search?pid=42&q=cough');
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
        $request = $this->makeRequest(
            token: 'not.a.real.jwt',
            pid: 42,
            q: 'cough',
        );

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create(
            '/agentforge/internal/notes_search?pid=42&q=cough',
        );
        $request->headers->set('Authorization', $token);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/notes_search?q=cough');
        $request->headers->set('Authorization', "Bearer {$token}");

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPidQueryParamIsZero(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 0,
            q: 'cough',
        );

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenPidDoesNotMatchTokenPatientId(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 99,
            q: 'cough',
        );

        self::assertSame(403, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenQQueryParamMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/notes_search?pid=42');
        $request->headers->set('Authorization', "Bearer {$token}");

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenQQueryParamIsEmpty(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: '',
        );

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenQQueryParamIsWhitespaceOnly(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: '   ',
        );

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returnsResultsArrayOnHappyPath(): void
    {
        $rawRows = [
            [
                'source' => 'pnote',
                'id' => 5,
                'date' => '2026-04-20 14:30:00',
                'title' => 'Phone call',
                'snippet' => 'Patient reports cough.',
                'score' => 1.234,
            ],
        ];
        $captured = [];
        $controller = $this->makeControllerWith(
            rows: $rawRows,
            captured: $captured,
        );
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
        );

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertInstanceOf(JsonResponse::class, $response);
        /** @var array{results: list<array<string, mixed>>} $body */
        $body = json_decode((string) $response->getContent(), true);
        self::assertCount(1, $body['results']);
        self::assertSame('pnote', $body['results'][0]['source']);
        self::assertSame(5, $body['results'][0]['id']);
        self::assertSame(1.234, $body['results'][0]['score']);
    }

    #[Test]
    public function trimsQQueryParamBeforeDispatch(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: '  cough  ',
        );

        $controller->show($request);

        self::assertSame('cough', $captured['params']['q']);
        self::assertSame(42, $captured['params']['pid']);
    }

    #[Test]
    public function defaultsLimitTo5WhenParamMissing(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
        );

        $controller->show($request);

        self::assertStringContainsString('LIMIT 5', $captured['sql']);
    }

    #[Test]
    public function honorsLimitQueryParamWhenInRange(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            limit: 7,
        );

        $controller->show($request);

        self::assertStringContainsString('LIMIT 7', $captured['sql']);
    }

    #[Test]
    public function clampsLimitAboveMaximum(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            limit: 999,
        );

        $controller->show($request);

        self::assertStringContainsString('LIMIT 10', $captured['sql']);
    }

    #[Test]
    public function fallsBackToDefaultLimitWhenZero(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            limit: 0,
        );

        $controller->show($request);

        // limit=0 falls back to the default 5 (not the clamped-min 1) —
        // see InternalNotesController.clampSinceDays for the same idiom.
        self::assertStringContainsString('LIMIT 5', $captured['sql']);
    }

    #[Test]
    public function defaultsSinceTo365DaysWhenParamMissing(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
        );

        $controller->show($request);

        self::assertSinceWindowMatches($captured['params']['since'], 365);
    }

    #[Test]
    public function honorsSinceQueryParamWhenInRange(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            since: 30,
        );

        $controller->show($request);

        self::assertSinceWindowMatches($captured['params']['since'], 30);
    }

    #[Test]
    public function clampsSinceQueryParamAboveMaximum(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            since: 99999,
        );

        $controller->show($request);

        self::assertSinceWindowMatches($captured['params']['since'], 365);
    }

    #[Test]
    public function fallsBackToDefaultSinceWhenZero(): void
    {
        $captured = [];
        $controller = $this->makeControllerWith(rows: [], captured: $captured);
        $request = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            pid: 42,
            q: 'cough',
            since: 0,
        );

        $controller->show($request);

        // since_days=0 falls back to the default 365 (not the clamped-min 1).
        self::assertSinceWindowMatches($captured['params']['since'], 365);
    }

    /**
     * Assert that `since` is roughly $expectedDays old (within 60s slop for
     * test runtime).
     */
    private static function assertSinceWindowMatches(
        string $sinceParam,
        int $expectedDays,
    ): void {
        $sinceTimestamp = strtotime($sinceParam);
        self::assertNotFalse($sinceTimestamp);
        $expectedSeconds = $expectedDays * 86400;
        $now = time();
        self::assertGreaterThanOrEqual(
            $now - $expectedSeconds - 60,
            $sinceTimestamp,
            "since '{$sinceParam}' is older than expected {$expectedDays}d window",
        );
        self::assertLessThanOrEqual(
            $now - $expectedSeconds + 60,
            $sinceTimestamp,
            "since '{$sinceParam}' is newer than expected {$expectedDays}d window",
        );
    }

    private function makeController(): InternalNotesSearchController
    {
        $captured = [];
        return $this->makeControllerWith(rows: [], captured: $captured);
    }

    /**
     * Build a controller wired to a real NotesSearchRepository whose
     * Connection is mocked so the test can capture the SQL/params.
     *
     * @param list<array<string, mixed>> $rows
     * @param array<empty>                                                              $captured
     * @param-out array{sql: string, params: array{pid: int, q: string, since: string}} $captured
     *
     * The fetchAllAssociative mock writes the executed SQL and bound params
     * into $captured (out-parameter) so each test can assert on the wire-level
     * query shape. The @param-out shape is what callers see after the call;
     * input is always an empty array. 'q' is the trimmed search query.
     */
    private function makeControllerWith(
        array $rows,
        array &$captured,
    ): InternalNotesSearchController {
        // Dummy init matching the @param-out shape so PHPStan sees a
        // consistent type at every program point. Real values come in
        // when fetchAllAssociative fires below.
        $captured = [
            'sql' => '',
            'params' => ['pid' => 0, 'q' => '', 'since' => ''],
        ];

        $connection = self::createMock(Connection::class);
        $connection
            ->method('fetchAllAssociative')
            ->willReturnCallback(function (string $sql, array $params) use (
                &$captured,
                $rows
            ): array {
                /** @var array{pid: int, q: string, since: string} $params */
                $captured = ['sql' => $sql, 'params' => $params];
                return $rows;
            });

        return new InternalNotesSearchController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            new NotesSearchRepository($connection),
        );
    }

    private function makeRequest(
        string $token,
        int $pid,
        string $q,
        ?int $limit = null,
        ?int $since = null,
    ): Request {
        $params = ['pid' => (string) $pid, 'q' => $q];
        if ($limit !== null) {
            $params['limit'] = (string) $limit;
        }
        if ($since !== null) {
            $params['since_days'] = (string) $since;
        }
        $query = http_build_query($params);
        $request = Request::create("/agentforge/internal/notes_search?{$query}");
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
