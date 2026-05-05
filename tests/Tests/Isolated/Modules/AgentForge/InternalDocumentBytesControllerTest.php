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
use OpenEMR\Modules\AgentForge\Controllers\InternalDocumentBytesController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentBytesRepository;
use OpenEMR\Modules\AgentForge\Services\DocumentBytesResult;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalDocumentBytesController.
 *
 * Mirrors the existing Internal*ControllerTest shape: JWT validation
 * (missing / empty / malformed / wrong-scheme), document_id parsing,
 * repository lookup, JWT-vs-document patient-scope check, and
 * response shape (binary bytes with the correct Content-Type and
 * cache-disabling headers).
 *
 * The patient-scope check is the load-bearing privacy invariant: a
 * sidecar bug that requested someone else's document_id should land
 * as a 403, never a 200 with mismatched bytes.
 */
final class InternalDocumentBytesControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-05-05T15:00:00+00:00';

    // ---------------------------------------------------------------
    // JWT validation — 401 paths
    // ---------------------------------------------------------------

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/get_document_bytes?document_id=42');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/get_document_bytes?document_id=42');
        $request->headers->set('Authorization', '');

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerTokenIsMalformed(): void
    {
        // Catches the InvalidTokenStructure case: a bearer that isn't a
        // JWT shape at all should land as 401, not 500.
        $controller = $this->makeController();
        $request = $this->makeRequest(token: 'not.a.real.jwt', documentId: 42);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42);
        $request = Request::create('/agentforge/internal/get_document_bytes?document_id=42');
        $request->headers->set('Authorization', $token);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenTokenSignatureIsInvalid(): void
    {
        // Token signed with a different secret.
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText('fedcba9876543210fedcba9876543210'),
        );
        $now = new DateTimeImmutable(self::TEST_NOW);
        $badToken = $config->builder()
            ->issuedBy('openemr-agentforge')
            ->relatedTo('99')
            ->withClaim('patient_id', 42)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($config->signer(), $config->signingKey())
            ->toString();

        $controller = $this->makeController();
        $request = $this->makeRequest(token: $badToken, documentId: 42);

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // document_id query parameter — 400 paths
    // ---------------------------------------------------------------

    #[Test]
    public function returns400WhenDocumentIdMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/get_document_bytes');
        $request->headers->set('Authorization', 'Bearer ' . $this->mintToken(patientId: 42));

        $response = $controller->show($request);
        self::assertSame(400, $response->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocumentIdIsZero(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 0);

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocumentIdIsNegative(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: -1);

        self::assertSame(400, $controller->show($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 404 — document doesn't exist
    // ---------------------------------------------------------------

    #[Test]
    public function returns404WhenDocumentNotFound(): void
    {
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->expects(self::once())
            ->method('findById')
            ->with(99999)
            ->willReturn(null);

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 99999);

        $response = $controller->show($request);
        self::assertSame(404, $response->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 403 — JWT-vs-document patient mismatch (the load-bearing check)
    // ---------------------------------------------------------------

    #[Test]
    public function returns403WhenDocumentBelongsToDifferentPatient(): void
    {
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->expects(self::once())
            ->method('findById')
            ->with(42)
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 99, // different from the JWT's patient_id below
                mimetype: 'application/pdf',
                bytes: 'should never reach the response',
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);

        $response = $controller->show($request);
        self::assertSame(403, $response->getStatusCode());
        // The bytes must NOT have leaked into the response body.
        self::assertNotSame('should never reach the response', (string) $response->getContent());
    }

    // ---------------------------------------------------------------
    // 200 — happy path bytes streaming
    // ---------------------------------------------------------------

    #[Test]
    public function returns200WithPdfBytesWhenAuthorized(): void
    {
        $bytes = "%PDF-1.4\n%fake-pdf-bytes\n";
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->expects(self::once())
            ->method('findById')
            ->with(42)
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: $bytes,
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame('application/pdf', $response->headers->get('Content-Type'));
        self::assertSame((string) strlen($bytes), $response->headers->get('Content-Length'));
        self::assertSame($bytes, (string) $response->getContent());
    }

    #[Test]
    public function returns200WithImageMimetypeWhenDocumentIsImage(): void
    {
        $bytes = "\x89PNG\r\n\x1a\nfake-png-bytes";
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 7,
                patientId: 7,
                mimetype: 'image/png',
                bytes: $bytes,
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 7), documentId: 7);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame('image/png', $response->headers->get('Content-Type'));
    }

    #[Test]
    public function disablesIntermediaryCacheOnSuccessfulResponse(): void
    {
        // Defense in depth: a shared cache hit on a previous patient's
        // bytes would invert the JWT-vs-patient scope check. Cache-Control
        // must explicitly forbid storage on the way back.
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 1,
                patientId: 1,
                mimetype: 'application/pdf',
                bytes: 'x',
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 1), documentId: 1);

        $cacheControl = (string) $controller->show($request)->headers->get('Cache-Control');
        self::assertStringContainsString('no-store', $cacheControl);
        self::assertStringContainsString('private', $cacheControl);
    }

    // ---------------------------------------------------------------
    // Test fixtures
    // ---------------------------------------------------------------

    private function makeController(
        ?DocumentBytesRepository $repository = null,
    ): InternalDocumentBytesController {
        $repository ??= self::createMock(DocumentBytesRepository::class);
        return new InternalDocumentBytesController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $repository,
        );
    }

    private function makeRequest(string $token, int $documentId): Request
    {
        $request = Request::create("/agentforge/internal/get_document_bytes?document_id={$documentId}");
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
            ->relatedTo((string) $patientId)
            ->withClaim('patient_id', $patientId)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($config->signer(), $config->signingKey())
            ->toString();
    }
}
