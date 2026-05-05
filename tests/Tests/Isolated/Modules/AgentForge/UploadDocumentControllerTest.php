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

use OpenEMR\Modules\AgentForge\Controllers\UploadDocumentController;
use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use PHPUnit\Framework\MockObject\MockObject;
use RuntimeException;
use Symfony\Component\HttpFoundation\File\UploadedFile;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;

/**
 * Behavior tests for UploadDocumentController.
 *
 * Covers the Task 6 test strategy:
 *
 *   1. 201 happy path: writer returns id, audit fires
 *   2. 401 when session has no authUserID/authUser
 *   3. 400 when session has no pid
 *   4. 400 when payload patient_id mismatches session pid (NO audit)
 *   5. 400 when payload patient_id matches session pid → succeeds
 *   6. 400 when doc_type is missing or unsupported
 *   7. 400 when file is missing or empty
 *   8. 400 when uploaded bytes don't start with PDF magic
 *   9. 500 when DocumentUploadWriter throws
 *   10. Audit captures session-derived patient_id, NOT the payload one
 *   11. Optional encounter_id is forwarded to the writer when valid
 */
final class UploadDocumentControllerTest extends TestCase
{
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

    // -------------------------------------------------------------------
    // 201 — happy path
    // -------------------------------------------------------------------

    #[Test]
    public function returns201AndFiresAuditOnSuccessfulUpload(): void
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        // ``with()`` is @no-named-arguments; positional only. Order
        // matches DocumentUploadWriter::upload's parameter list:
        // patientId, docType, filename, mimetype, bytes, ownerUserId,
        // encounterId.
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

        $audit = $this->createMock(DocumentIngestAuditWriter::class);
        // record's parameter order: userId, patientId, documentId,
        // docType, breakglassFlag, breakglassReason.
        $audit->expects(self::once())
            ->method('record')
            ->with(99, 42, 123, 'lab_pdf', false, null);

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            filename: 'demo-lab.pdf',
        );

        $response = $controller->upload($request);

        self::assertSame(201, $response->getStatusCode());
        $body = $this->decodeJsonBody($response);
        self::assertTrue($body['success'] ?? null);
        self::assertSame(123, $body['document_id'] ?? null);
    }

    #[Test]
    public function passesPayloadPatientIdWhenItMatchesSessionPid(): void
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        $writer->expects(self::once())->method('upload')->willReturn(7);
        $audit = $this->createMock(DocumentIngestAuditWriter::class);
        $audit->expects(self::once())->method('record');

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'intake_form',
            payloadPatientId: '42', // matches session — accepted
        );

        self::assertSame(201, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function forwardsEncounterIdToWriterWhenProvided(): void
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        // Positional only: encounterId is the 7th parameter.
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

        $audit = $this->createMock(DocumentIngestAuditWriter::class);
        $audit->expects(self::once())->method('record');

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 1,
            sessionUserId: 1,
            sessionUsername: 'u',
            docType: 'lab_pdf',
            encounterId: '555',
        );

        self::assertSame(201, $controller->upload($request)->getStatusCode());
    }

    // -------------------------------------------------------------------
    // 401 — session auth
    // -------------------------------------------------------------------

    #[Test]
    public function returns401WhenSessionLacksAuthUserId(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: null,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
        );

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenSessionLacksAuthUsername(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: '',
            docType: 'lab_pdf',
        );

        self::assertSame(401, $controller->upload($request)->getStatusCode());
    }

    // -------------------------------------------------------------------
    // 400 — patient context
    // -------------------------------------------------------------------

    #[Test]
    public function returns400WhenSessionHasNoPid(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: null,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
        );

        $response = $controller->upload($request);
        self::assertSame(400, $response->getStatusCode());
        self::assertStringContainsString(
            'No patient context',
            (string) $response->getContent(),
        );
    }

    #[Test]
    public function returns400AndDoesNotFireAuditOnPatientIdMismatch(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            payloadPatientId: '7', // mismatches session pid 42
        );

        $response = $controller->upload($request);
        self::assertSame(400, $response->getStatusCode());
        self::assertStringContainsString(
            'Patient ID mismatch',
            (string) $response->getContent(),
        );
    }

    #[Test]
    public function returns400WhenPayloadPatientIdIsNonInteger(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            payloadPatientId: 'not-a-number',
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    // -------------------------------------------------------------------
    // 400 — multipart validation
    // -------------------------------------------------------------------

    #[Test]
    public function returns400WhenDocTypeIsMissing(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: null,
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocTypeIsUnsupported(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'discharge_summary',
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenFileIsMissing(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            includeFile: false,
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenFileBytesAreNotPdf(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            fileMagic: 'JFIF junk', // JPEG-ish, not PDF
        );

        $response = $controller->upload($request);
        self::assertSame(400, $response->getStatusCode());
        self::assertStringContainsString(
            'not a valid PDF',
            (string) $response->getContent(),
        );
    }

    #[Test]
    public function returns400WhenEncounterIdIsNonPositive(): void
    {
        $controller = new UploadDocumentController(
            $this->expectNeverCalledWriter(),
            $this->expectNeverCalledAudit(),
        );
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            encounterId: '0',
        );

        self::assertSame(400, $controller->upload($request)->getStatusCode());
    }

    // -------------------------------------------------------------------
    // 500 — writer failure
    // -------------------------------------------------------------------

    #[Test]
    public function returns500AndOmitsLegacyMessageWhenWriterThrows(): void
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        $writer->method('upload')->willThrowException(
            new RuntimeException('disk encryption failed: /var/lib/openemr/secret-path'),
        );
        $audit = $this->expectNeverCalledAudit();

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
        );

        $response = $controller->upload($request);
        self::assertSame(500, $response->getStatusCode());
        // Legacy error must NOT leak to the user — it can include
        // filesystem paths (PHI exposure risk).
        self::assertStringNotContainsString(
            'disk encryption failed',
            (string) $response->getContent(),
        );
        self::assertStringNotContainsString(
            'secret-path',
            (string) $response->getContent(),
        );
    }

    // -------------------------------------------------------------------
    // Audit-derived-patient-id discipline
    // -------------------------------------------------------------------

    #[Test]
    public function auditUsesSessionPidNotPayloadPid(): void
    {
        // Even when payload patient_id matches session pid, the audit
        // call must read the session value — guards against a future
        // refactor that accidentally swaps to the payload value.
        $writer = $this->createMock(DocumentUploadWriter::class);
        $writer->method('upload')->willReturn(50);

        $audit = $this->createMock(DocumentIngestAuditWriter::class);
        // record(): userId, patientId, documentId, docType, breakglassFlag,
        // breakglassReason. patientId is the 2nd positional arg — that's
        // the session value we're locking, the rest accept anything.
        $audit->expects(self::once())
            ->method('record')
            ->with(
                self::anything(),
                42, // session pid, not payload value
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
            );

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            payloadPatientId: '42',
        );

        $controller->upload($request);
    }

    #[Test]
    public function auditCarriesBreakglassContextWhenSessionHasIt(): void
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        $writer->method('upload')->willReturn(7);

        $audit = $this->createMock(DocumentIngestAuditWriter::class);
        // 5th + 6th args are breakglassFlag + breakglassReason.
        $audit->expects(self::once())
            ->method('record')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                true,
                'Patient unresponsive in ED',
            );

        $controller = new UploadDocumentController($writer, $audit);
        $request = $this->makeRequest(
            sessionPid: 42,
            sessionUserId: 99,
            sessionUsername: 'admin',
            docType: 'lab_pdf',
            sessionBreakglassFlag: true,
            sessionBreakglassReason: 'Patient unresponsive in ED',
        );

        $controller->upload($request);
    }

    // -------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------

    /**
     * Build a Request with multipart fields, a PDF file (or one whose
     * magic bytes are deliberately wrong), and a Symfony Session
     * carrying the provided OpenEMR-style keys.
     */
    private function makeRequest(
        int|null $sessionPid,
        int|null $sessionUserId,
        string $sessionUsername,
        string|null $docType,
        string $filename = 'upload.pdf',
        string|null $payloadPatientId = null,
        string|null $encounterId = null,
        bool $includeFile = true,
        string $fileMagic = '%PDF-1.4 sample body',
        bool $sessionBreakglassFlag = false,
        string|null $sessionBreakglassReason = null,
    ): Request {
        $post = [];
        if ($docType !== null) {
            $post['doc_type'] = $docType;
        }
        if ($payloadPatientId !== null) {
            $post['patient_id'] = $payloadPatientId;
        }
        if ($encounterId !== null) {
            $post['encounter_id'] = $encounterId;
        }

        $files = [];
        if ($includeFile) {
            $files['file'] = $this->makeUploadedFile($filename, $fileMagic);
        }

        $request = Request::create(
            uri: '/agentforge/upload_document',
            method: 'POST',
            parameters: $post,
            files: $files,
        );

        $session = new Session(new MockArraySessionStorage());
        $session->start();
        if ($sessionPid !== null) {
            $session->set('pid', $sessionPid);
        }
        if ($sessionUserId !== null) {
            $session->set('authUserID', $sessionUserId);
        }
        $session->set('authUser', $sessionUsername);
        $session->set('breakglass_flag', $sessionBreakglassFlag);
        if ($sessionBreakglassReason !== null) {
            $session->set('breakglass_reason', $sessionBreakglassReason);
        }
        $request->setSession($session);

        return $request;
    }

    /**
     * Materialize a temp file with the given magic bytes and wrap it
     * in an UploadedFile in test mode (skips is_uploaded_file()).
     */
    private function makeUploadedFile(string $filename, string $magic): UploadedFile
    {
        $tmp = tempnam(sys_get_temp_dir(), 'agentforge-upload-');
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

    private function expectNeverCalledWriter(): DocumentUploadWriter&MockObject
    {
        $writer = $this->createMock(DocumentUploadWriter::class);
        $writer->expects(self::never())->method(self::anything());
        return $writer;
    }

    private function expectNeverCalledAudit(): DocumentIngestAuditWriter&MockObject
    {
        $audit = $this->createMock(DocumentIngestAuditWriter::class);
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
