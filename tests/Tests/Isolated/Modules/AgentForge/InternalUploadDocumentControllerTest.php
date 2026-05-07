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
use OpenEMR\Modules\AgentForge\Controllers\InternalUploadDocumentController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\File\UploadedFile;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalUploadDocumentController (T38.15).
 *
 * The controller is the JWT-authed sibling of UploadDocumentController:
 * the BFF (sidecar) forwards a user-bound internal JWT plus a
 * ``patient_uuid`` multipart field, and we resolve the UUID against
 * :class:`PatientPidRepository`, then enforce that the resolved pid
 * matches the JWT's ``patient_id`` claim before writing.
 *
 * Failure mode coverage parallels InternalDocumentBytesControllerTest:
 *
 *   401 — missing / empty / malformed / wrong-scheme / wrong-secret JWT
 *   400 — missing patient_uuid, missing/invalid file, bad doc_type, magic-byte fail
 *   404 — patient_uuid resolves to no pid
 *   403 — resolved pid mismatches JWT.patient_id (load-bearing privacy check)
 *   500 — DocumentUploadWriter throws (legacy error must NOT leak)
 *   200 — happy path: writer returns id, audit fires with JWT-derived pid
 */
final class InternalUploadDocumentControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-05-05T15:00:00+00:00';

    /** @var array<string> */
    private array $tempFiles = [];

    protected function tearDown(): void
    {
        foreach ($this->tempFiles as $path) {
            if (is_file($path)) {
                @unlink($path);
            }
        }
        $this->tempFiles = [];
        parent::tearDown();
    }

    // ---------------------------------------------------------------
    // JWT validation — 401 paths
    // ---------------------------------------------------------------

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(token: null, patientUuid: 'abc');

        $response = $controller->upload($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(401, $response->getStatusCode());
    }

    #[Test]
    public function returns401WhenAuthorizationHeaderEmpty(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(token: '', patientUuid: 'abc');

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerTokenIsMalformed(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(token: 'Bearer not.a.real.jwt', patientUuid: 'abc');

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeIsMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42, userId: 99);
        $request = $this->makeMultipartRequest(token: $token, patientUuid: 'abc');

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenTokenSignedWithDifferentSecret(): void
    {
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
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $badToken,
            patientUuid: 'abc',
        );

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // patient_uuid + multipart validation — 400 paths
    // ---------------------------------------------------------------

    #[Test]
    public function returns400WhenPatientUuidMissing(): void
    {
        $controller = $this->makeController();
        $token = $this->mintToken(patientId: 42, userId: 99);
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $token,
            patientUuid: null,
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocTypeIsMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            docType: null,
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocTypeIsUnsupported(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            docType: 'discharge_summary',
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenFileIsMissing(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            includeFile: false,
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenFileBytesAreNotPdf(): void
    {
        $controller = $this->makeController();
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            fileMagic: 'JFIF junk',
        );

        $response = $controller->upload($request);
        self::assertSame(400, $response->getStatusCode());
        self::assertStringContainsString(
            'not a valid PDF',
            (string) $response->getContent(),
        );
    }

    // ---------------------------------------------------------------
    // 404 — patient_uuid doesn't resolve
    // ---------------------------------------------------------------

    #[Test]
    public function returns404WhenPatientUuidDoesNotResolve(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->method('findPidByUuid')->willReturn(null);

        $controller = $this->makeController(patientRepo: $repo);
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'unknown-uuid',
        );

        self::assertSame(404, $controller->upload($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 403 — JWT-vs-patient mismatch (the load-bearing privacy check)
    // ---------------------------------------------------------------

    #[Test]
    public function returns403WhenResolvedPidMismatchesJwtPatientClaim(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        // UUID resolves to pid=99 but JWT was minted for patient_id=42:
        // a sidecar bug or a tampering attempt should land 403, never 200.
        $repo->method('findPidByUuid')->willReturn(99);

        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();

        $controller = $this->makeController(
            patientRepo: $repo,
            writer: $writer,
            audit: $audit,
        );
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'cross-patient-uuid',
        );

        self::assertSame(403, $controller->upload($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 500 — writer failure
    // ---------------------------------------------------------------

    #[Test]
    public function returns500AndOmitsLegacyMessageWhenWriterThrows(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->method('findPidByUuid')->willReturn(42);

        $writer = self::createMock(DocumentUploadWriter::class);
        $writer->method('upload')->willThrowException(
            new RuntimeException('disk encryption failed: /var/lib/openemr/secret-path'),
        );
        $audit = $this->expectNeverCalledAudit();

        $controller = $this->makeController(
            patientRepo: $repo,
            writer: $writer,
            audit: $audit,
        );
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
        );

        $response = $controller->upload($request);
        self::assertSame(500, $response->getStatusCode());
        self::assertStringNotContainsString(
            'disk encryption failed',
            (string) $response->getContent(),
        );
        self::assertStringNotContainsString(
            'secret-path',
            (string) $response->getContent(),
        );
    }

    // ---------------------------------------------------------------
    // 200 — happy path
    // ---------------------------------------------------------------

    #[Test]
    public function returns200WithDocumentIdAndFiresAuditOnSuccessfulUpload(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->method('findPidByUuid')->willReturn(42);

        $writer = self::createMock(DocumentUploadWriter::class);
        // Positional only; matches DocumentUploadWriter::upload's order.
        $writer->expects(self::once())
            ->method('upload')
            ->with(
                42,
                'lab_pdf',
                'demo-lab.pdf',
                'application/pdf',
                self::anything(),
                99,
                null,
            )
            ->willReturn(123);

        $audit = self::createMock(DocumentIngestAuditWriter::class);
        // record's parameter order: userId, patientId, documentId,
        // docType, breakglassFlag, breakglassReason. patient_id MUST
        // come from the JWT claim, not the payload — this assertion
        // locks that.
        $audit->expects(self::once())
            ->method('record')
            ->with(99, 42, 123, 'lab_pdf', false, null);

        $controller = $this->makeController(
            patientRepo: $repo,
            writer: $writer,
            audit: $audit,
        );
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            docType: 'lab_pdf',
            filename: 'demo-lab.pdf',
        );

        $response = $controller->upload($request);

        self::assertSame(200, $response->getStatusCode());
        $body = $this->decodeJsonBody($response);
        self::assertSame(123, $body['document_id'] ?? null);
    }

    #[Test]
    public function forwardsEncounterIdToWriterWhenProvided(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->method('findPidByUuid')->willReturn(42);

        $writer = self::createMock(DocumentUploadWriter::class);
        $writer->expects(self::once())
            ->method('upload')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                555,
            )
            ->willReturn(8);

        $audit = self::createMock(DocumentIngestAuditWriter::class);
        $audit->expects(self::once())->method('record');

        $controller = $this->makeController(
            patientRepo: $repo,
            writer: $writer,
            audit: $audit,
        );
        $request = $this->makeMultipartRequest(
            token: 'Bearer ' . $this->mintToken(patientId: 42, userId: 99),
            patientUuid: 'abc',
            encounterId: '555',
        );

        self::assertSame(200, $controller->upload($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // Test fixtures
    // ---------------------------------------------------------------

    private function makeController(
        ?PatientPidRepository $patientRepo = null,
        ?DocumentUploadWriter $writer = null,
        ?DocumentIngestAuditWriter $audit = null,
    ): InternalUploadDocumentController {
        return new InternalUploadDocumentController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $patientRepo ?? self::createMock(PatientPidRepository::class),
            $writer ?? self::createMock(DocumentUploadWriter::class),
            $audit ?? self::createMock(DocumentIngestAuditWriter::class),
        );
    }

    private function makeMultipartRequest(
        ?string $token,
        ?string $patientUuid,
        ?string $docType = 'lab_pdf',
        string $filename = 'upload.pdf',
        ?string $encounterId = null,
        bool $includeFile = true,
        string $fileMagic = '%PDF-1.4 sample body',
    ): Request {
        $post = [];
        if ($patientUuid !== null) {
            $post['patient_uuid'] = $patientUuid;
        }
        if ($docType !== null) {
            $post['doc_type'] = $docType;
        }
        if ($encounterId !== null) {
            $post['encounter_id'] = $encounterId;
        }

        $files = [];
        if ($includeFile) {
            $files['file'] = $this->makeUploadedFile($filename, $fileMagic);
        }

        $request = Request::create(
            uri: '/agentforge/internal/upload_document',
            method: 'POST',
            parameters: $post,
            files: $files,
        );

        if ($token !== null) {
            $request->headers->set('Authorization', $token);
        }

        return $request;
    }

    private function makeUploadedFile(string $filename, string $magic): UploadedFile
    {
        $tmp = tempnam(sys_get_temp_dir(), 'agentforge-internal-upload-');
        if ($tmp === false) {
            self::fail('Could not create temp file for UploadedFile fixture');
        }
        file_put_contents($tmp, $magic);
        $this->tempFiles[] = $tmp;

        return new UploadedFile(
            path: $tmp,
            originalName: $filename,
            mimeType: 'application/pdf',
            error: null,
            test: true,
        );
    }

    private function mintToken(int $patientId, int $userId): string
    {
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::TEST_SECRET),
        );
        $now = new DateTimeImmutable(self::TEST_NOW);

        return $config->builder()
            ->issuedBy('openemr-agentforge')
            ->relatedTo((string) $userId)
            ->withClaim('patient_id', $patientId)
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($config->signer(), $config->signingKey())
            ->toString();
    }

    private function expectNeverCalledWriter(): DocumentUploadWriter&MockObject
    {
        $writer = self::createMock(DocumentUploadWriter::class);
        $writer->expects(self::never())->method(self::anything());
        return $writer;
    }

    private function expectNeverCalledAudit(): DocumentIngestAuditWriter&MockObject
    {
        $audit = self::createMock(DocumentIngestAuditWriter::class);
        $audit->expects(self::never())->method(self::anything());
        return $audit;
    }

    /**
     * @return array<string, mixed>
     */
    private function decodeJsonBody(JsonResponse $response): array
    {
        $raw = (string) $response->getContent();
        $decoded = json_decode($raw, associative: true);
        self::assertIsArray($decoded, 'Response body must be JSON object');
        $narrowed = [];
        foreach ($decoded as $key => $value) {
            self::assertIsString($key, 'JSON keys must be strings');
            $narrowed[$key] = $value;
        }
        return $narrowed;
    }
}
