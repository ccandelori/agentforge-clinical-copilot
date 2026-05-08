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
    public function emitsPrivateRevalidatingCacheControlOnSuccess(): void
    {
        // After Task 26 the response opts into a short-lived per-user
        // cache so the citation-overlay re-opens don't redownload the
        // bytes on every paint. ``private`` is mandatory — a shared
        // cache hit on a previous patient's bytes would invert the
        // JWT-vs-patient scope check. ``must-revalidate`` forces the
        // client to re-validate via ``If-None-Match`` before serving
        // stale, so a revoked document can't be replayed indefinitely.
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
        self::assertStringContainsString('private', $cacheControl);
        self::assertStringContainsString('max-age=300', $cacheControl);
        self::assertStringContainsString('must-revalidate', $cacheControl);
    }

    #[Test]
    public function neverEmitsPublicCacheControlEvenAcrossPatients(): void
    {
        // PHI-safety regression guard: regardless of patient or document
        // identity, the response MUST NOT advertise itself as cacheable
        // by a shared (proxy / CDN) cache. ``public`` would be the
        // canonical leak; assert its absence directly so a future
        // refactor to a Symfony header bag doesn't accidentally flip
        // the visibility.
        foreach ([1, 7, 999] as $patientId) {
            $repository = self::createMock(DocumentBytesRepository::class);
            $repository
                ->method('findById')
                ->willReturn(new DocumentBytesResult(
                    documentId: $patientId,
                    patientId: $patientId,
                    mimetype: 'application/pdf',
                    bytes: 'patient-' . $patientId,
                ));

            $controller = $this->makeController(repository: $repository);
            $request = $this->makeRequest(
                token: $this->mintToken(patientId: $patientId),
                documentId: $patientId,
            );

            $response = $controller->show($request);
            $cacheControl = (string) $response->headers->get('Cache-Control');

            self::assertSame(200, $response->getStatusCode());
            self::assertStringContainsString('private', $cacheControl);
            self::assertStringNotContainsString('public', $cacheControl);
        }
    }

    // ---------------------------------------------------------------
    // ETag emission + 304 conditional GET (Task 26)
    // ---------------------------------------------------------------

    #[Test]
    public function emitsEtagHeaderOnSuccessfulResponse(): void
    {
        // The citation overlay re-opens the same document on every
        // chip click. An ETag turns the second-and-subsequent fetches
        // into cheap 304s, which keeps the round-trip latency from
        // dominating the user-perceived overlay open time.
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: '%PDF-1.4 etag-fixture-bytes',
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        $etag = $response->headers->get('ETag');
        self::assertNotNull($etag, 'ETag header must be present on 200 responses');
        // Quoted strong validator per RFC 7232 §2.3 — Symfony adds the
        // quotes itself when handed an unquoted value, so we accept
        // either spelling here and tighten the strong-validator check
        // in a separate test.
        self::assertMatchesRegularExpression('/^(W\/)?"[^"]+"$/', (string) $etag);
    }

    #[Test]
    public function etagIsStableAcrossRequestsForSameDocument(): void
    {
        // The whole point of the ETag is that the second request's
        // ``If-None-Match`` matches the first response's emitted
        // value. If the ETag rotates per request the 304 path never
        // fires.
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: 'identical-bytes-on-both-fetches',
            ));

        $controller = $this->makeController(repository: $repository);

        $first = $controller->show($this->makeRequest(
            token: $this->mintToken(patientId: 42),
            documentId: 42,
        ));
        $second = $controller->show($this->makeRequest(
            token: $this->mintToken(patientId: 42),
            documentId: 42,
        ));

        self::assertSame(
            $first->headers->get('ETag'),
            $second->headers->get('ETag'),
        );
    }

    #[Test]
    public function etagDiffersWhenBytesDiffer(): void
    {
        // A revised document (different bytes) must produce a
        // different ETag so a stale cache entry is invalidated by
        // the client's revalidation step. Without this the
        // ``must-revalidate`` directive is toothless.
        $repositoryA = self::createMock(DocumentBytesRepository::class);
        $repositoryA
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: 'original-bytes',
            ));
        $repositoryB = self::createMock(DocumentBytesRepository::class);
        $repositoryB
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: 'revised-bytes',
            ));

        $token = $this->mintToken(patientId: 42);
        $etagA = $this->makeController(repository: $repositoryA)
            ->show($this->makeRequest(token: $token, documentId: 42))
            ->headers->get('ETag');
        $etagB = $this->makeController(repository: $repositoryB)
            ->show($this->makeRequest(token: $token, documentId: 42))
            ->headers->get('ETag');

        self::assertNotSame($etagA, $etagB);
    }

    #[Test]
    public function returns304WhenIfNoneMatchEqualsCurrentEtag(): void
    {
        // Conditional-GET happy path: client sends back the ETag we
        // gave it last time, server returns 304 with no body. This is
        // where the latency win lives — the bytes (often O(MB)) stay
        // on the server.
        $bytes = 'document-bytes-for-conditional-get';
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: $bytes,
            ));

        $controller = $this->makeController(repository: $repository);

        // First fetch — capture the ETag the server emitted.
        $first = $controller->show($this->makeRequest(
            token: $this->mintToken(patientId: 42),
            documentId: 42,
        ));
        $etag = (string) $first->headers->get('ETag');
        self::assertNotSame('', $etag);

        // Second fetch with that ETag echoed back via If-None-Match.
        $second = $this->makeRequest(
            token: $this->mintToken(patientId: 42),
            documentId: 42,
        );
        $second->headers->set('If-None-Match', $etag);

        $response = $controller->show($second);

        self::assertSame(304, $response->getStatusCode());
        // RFC 7232 §4.1: a 304 response MUST NOT contain a message body.
        self::assertSame('', (string) $response->getContent());
        // The validators / Cache-Control still need to ride along so
        // the client knows the cached entry is fresh for another
        // ``max-age`` window.
        self::assertSame($etag, $response->headers->get('ETag'));
        $cacheControl = (string) $response->headers->get('Cache-Control');
        self::assertStringContainsString('private', $cacheControl);
    }

    #[Test]
    public function returns200WhenIfNoneMatchDoesNotMatchCurrentEtag(): void
    {
        // Stale ETag (e.g. document was revised since the client
        // cached it) — server falls through to a normal 200 with the
        // fresh bytes and the new ETag.
        $bytes = 'fresh-bytes-after-revision';
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: $bytes,
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);
        $request->headers->set('If-None-Match', '"some-stale-etag-from-yesterday"');

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame($bytes, (string) $response->getContent());
        // The fresh ETag should NOT match the stale one the client sent.
        self::assertNotSame(
            '"some-stale-etag-from-yesterday"',
            $response->headers->get('ETag'),
        );
    }

    #[Test]
    public function returns200WhenIfNoneMatchHeaderAbsent(): void
    {
        // Cold-cache request — no If-None-Match — must still serve
        // bytes. Regression guard against a refactor that mistakenly
        // requires the conditional header.
        $bytes = 'cold-cache-bytes';
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 42,
                mimetype: 'application/pdf',
                bytes: $bytes,
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);
        // Explicitly no If-None-Match header.

        $response = $controller->show($request);

        self::assertSame(200, $response->getStatusCode());
        self::assertSame($bytes, (string) $response->getContent());
    }

    #[Test]
    public function ifNoneMatchOnAuthFailureStillReturns401(): void
    {
        // A 304 short-circuit MUST NOT bypass JWT validation —
        // otherwise an attacker who once observed a valid ETag could
        // skip auth entirely. Ordering: auth → conditional check.
        $controller = $this->makeController();
        $request = Request::create('/agentforge/internal/get_document_bytes?document_id=42');
        $request->headers->set('If-None-Match', '"any-etag"');
        // No Authorization header on purpose.

        self::assertSame(401, $controller->show($request)->getStatusCode());
    }

    #[Test]
    public function ifNoneMatchOnPatientMismatchStillReturns403(): void
    {
        // Same shape as the 401 case: the conditional-GET fast path
        // must run AFTER the JWT-vs-document patient-scope check, so
        // a stolen ETag for someone else's document still 403s.
        $repository = self::createMock(DocumentBytesRepository::class);
        $repository
            ->method('findById')
            ->willReturn(new DocumentBytesResult(
                documentId: 42,
                patientId: 99, // different from the JWT below
                mimetype: 'application/pdf',
                bytes: 'someone-elses-bytes',
            ));

        $controller = $this->makeController(repository: $repository);
        $request = $this->makeRequest(token: $this->mintToken(patientId: 42), documentId: 42);
        // Pretend the attacker captured a valid-looking ETag.
        $request->headers->set('If-None-Match', '"stolen-etag"');

        self::assertSame(403, $controller->show($request)->getStatusCode());
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
