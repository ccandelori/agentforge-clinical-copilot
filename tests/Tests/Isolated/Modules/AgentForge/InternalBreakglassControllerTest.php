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
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Modules\AgentForge\Controllers\InternalBreakglassController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\BreakglassAuditWriter;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalBreakglassController.
 *
 * The endpoint is the only POST internal endpoint AgentForge exposes.
 * Auth setup mirrors InternalNotesControllerTest (FrozenClock + minted
 * JWT). Because BreakglassAuditWriter is final, the test wires a real
 * writer backed by a mocked Connection + EventAuditLogger so the
 * controller→writer path is exercised end-to-end without database or
 * audit-log side effects.
 */
final class InternalBreakglassControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';
    private const DEFAULT_USER_ID = 42;
    private const DEFAULT_PATIENT_ID = 7;
    private const DEFAULT_REASON = 'Emergency department after-hours consult.';

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: null);

        self::assertSame(401, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: null);
        $request->headers->set('Authorization', '');

        self::assertSame(401, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerTokenIsMalformed(): void
    {
        // Catches the InvalidTokenStructure backport: a bearer that isn't a
        // JWT shape at all should land as 401, not 500. Without the
        // umbrella JwtException catch this falls through to a 500.
        $controller = $this->makeController();
        $request = $this->makeRequest(token: 'not.a.real.jwt');

        self::assertSame(401, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: null);
        $request->headers->set('Authorization', $this->mintToken());

        self::assertSame(401, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsEmpty(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(rawBody: '');

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsMalformedJson(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(rawBody: '{not-json');

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsNotJsonObject(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(rawBody: '"a string"');

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenUserIdMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenUserIdNotInteger(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => 'abc',
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenUserIdNonPositive(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => 0,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPatientIdMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPatientIdNonPositive(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => -1,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenReasonMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenReasonNotString(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => 12345,
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenReasonIsEmptyString(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => '',
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenReasonIsWhitespaceOnly(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => "   \t\n  ",
        ]);

        self::assertSame(400, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenUserIdDoesNotMatchTokenSub(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => 99,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(403, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenPatientIdDoesNotMatchTokenPatientIdClaim(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => 999,
            'reason' => self::DEFAULT_REASON,
        ]);

        self::assertSame(403, $controller->handle($request)->getStatusCode());
    }

    #[Test]
    public function returns201AndJsonBodyOnHappyPath(): void
    {
        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger->expects(self::once())->method('newEvent');

        $controller = $this->makeController(auditLogger: $auditLogger);
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        $response = $controller->handle($request);

        self::assertSame(201, $response->getStatusCode());
        self::assertInstanceOf(JsonResponse::class, $response);
        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        self::assertSame(['logged' => true], $body);
    }

    #[Test]
    public function happyPathInvokesAuditLoggerWithReasonAndPatientId(): void
    {
        // End-to-end: the controller must wire the body fields through to
        // EventAuditLogger->newEvent() with the reason text in the
        // PHI-bearing comments argument and the right patient_id.
        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::equalTo(BreakglassAuditWriter::EVENT_NAME),
                self::anything(),
                self::anything(),
                self::equalTo(1),
                self::stringContains(self::DEFAULT_REASON),
                self::equalTo(self::DEFAULT_PATIENT_ID),
            );

        $controller = $this->makeController(auditLogger: $auditLogger);
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => self::DEFAULT_REASON,
        ]);

        $controller->handle($request);
    }

    #[Test]
    public function happyPathTrimsLeadingAndTrailingWhitespaceFromReason(): void
    {
        $auditLogger = self::createMock(EventAuditLogger::class);
        $auditLogger
            ->expects(self::once())
            ->method('newEvent')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::callback(function (string $comments): bool {
                    // Reason must be trimmed before being embedded.
                    self::assertStringContainsString(self::DEFAULT_REASON, $comments);
                    self::assertStringNotContainsString("\t  " . self::DEFAULT_REASON, $comments);
                    self::assertStringNotContainsString(self::DEFAULT_REASON . "  \n", $comments);
                    return true;
                }),
            );

        $controller = $this->makeController(auditLogger: $auditLogger);
        $request = $this->makeRequest(body: [
            'user_id' => self::DEFAULT_USER_ID,
            'patient_id' => self::DEFAULT_PATIENT_ID,
            'reason' => "\t  " . self::DEFAULT_REASON . "  \n",
        ]);

        $controller->handle($request);
    }

    /**
     * Build a controller with a real BreakglassAuditWriter backed by
     * mocked Connection + EventAuditLogger so the controller→writer path
     * is exercised end-to-end without persistent side effects.
     */
    private function makeController(
        ?EventAuditLogger $auditLogger = null,
    ): InternalBreakglassController {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchOne')->willReturn('dr.smith');

        $auditLogger ??= self::createMock(EventAuditLogger::class);

        $writer = new BreakglassAuditWriter($connection, $auditLogger);

        return new InternalBreakglassController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $writer,
        );
    }

    /**
     * @param array<string, mixed>|null $body
     */
    private function makeRequest(
        ?string $token = 'default',
        ?array $body = null,
        ?string $rawBody = null,
    ): Request {
        $payload = $rawBody;
        if ($payload === null) {
            $payload = $body === null
                ? json_encode([
                    'user_id' => self::DEFAULT_USER_ID,
                    'patient_id' => self::DEFAULT_PATIENT_ID,
                    'reason' => self::DEFAULT_REASON,
                ])
                : json_encode($body);
        }
        if ($payload === false) {
            $payload = '';
        }

        $request = Request::create(
            uri: '/agentforge/internal/log_breakglass',
            method: 'POST',
            server: ['CONTENT_TYPE' => 'application/json'],
            content: $payload,
        );

        if ($token === 'default') {
            $request->headers->set('Authorization', 'Bearer ' . $this->mintToken());
        } elseif ($token !== null) {
            $request->headers->set('Authorization', 'Bearer ' . $token);
        }

        return $request;
    }

    private function mintToken(): string
    {
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::TEST_SECRET),
        );
        $now = new DateTimeImmutable(self::TEST_NOW);

        return $config->builder()
            ->issuedBy('openemr-agentforge')
            ->relatedTo((string) self::DEFAULT_USER_ID)
            ->withClaim('patient_id', self::DEFAULT_PATIENT_ID)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($config->signer(), $config->signingKey())
            ->toString();
    }
}
