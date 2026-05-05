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
use OpenEMR\Modules\AgentForge\Controllers\InternalLabPersistController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentOwnershipVerifier;
use OpenEMR\Modules\AgentForge\Services\LabPersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\LabResultIds;
use OpenEMR\Modules\AgentForge\Services\LabResultWriter;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalLabPersistController. Mirrors the intake
 * persistence controller's surface; the load-bearing assertions are
 * the same triple-check + audit-on-success-only invariants, with
 * additional coverage for the empty-values and multi-result paths.
 */
final class InternalLabPersistControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-05-05T15:00:00+00:00';

    // ---------------------------------------------------------------
    // 401 — JWT validation
    // ---------------------------------------------------------------

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = Request::create('/agentforge/internal/persist_lab_result', 'POST');

        self::assertSame(401, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerMalformed(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = $this->makeRequest(token: 'not.a.real.jwt', body: $this->validBody());

        self::assertSame(401, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 400 — body validation
    // ---------------------------------------------------------------

    #[Test]
    public function returns400WhenBodyEmpty(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(token: $this->mintToken(42, 99), bodyRaw: '');

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenInvalidJson(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(42, 99),
            bodyRaw: '{not-json',
        );

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocumentIdMissing(): void
    {
        $controller = $this->makeController();
        $body = $this->validBody();
        unset($body['document_id']);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenValuesArrayEmpty(): void
    {
        // A blank lab PDF is a valid LabPdfExtraction shape, but
        // persisting zero rows would create a dangling
        // procedure_order/report — fail at the controller. Schema
        // validity != persistence validity here.
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['values'] = [];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenValuesNotArray(): void
    {
        $controller = $this->makeController();
        $body = $this->validBody();
        $body['values'] = 'not an array';
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 403 — triple-check failures
    // ---------------------------------------------------------------

    #[Test]
    public function returns403WhenJwtPatientIdDiffersFromRequestPayload(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->expects(self::never())->method('findOwningPatientId');

        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $body = $this->validBody();
        $body['patient_id'] = 99; // JWT carries 42
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenDocumentForeignIdDiffersFromJwtPatient(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->expects(self::once())
            ->method('findOwningPatientId')
            ->with(123)
            ->willReturn(99);

        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenDocumentNotFound(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(null);

        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 201 — happy paths
    // ---------------------------------------------------------------

    #[Test]
    public function returns201WithCreatedIdsOnSuccessfulPersist(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $writer = self::createMock(LabResultWriter::class);
        $writer->expects(self::once())
            ->method('persist')
            ->with(
                self::equalTo(42),                              // patientId
                self::equalTo(99),                              // userId (JWT sub)
                self::equalTo(123),                             // documentId
                self::equalTo('Dr. Smith'),                     // orderingProvider
                self::equalTo('ACC-1'),                         // accessionNumber
                self::callback(static function (array $values): bool {
                    if (count($values) !== 1) {
                        return false;
                    }
                    $first = $values[0];
                    return is_array($first) && ($first['test_name'] ?? null) === 'Glucose';
                }),
            )
            ->willReturn(new LabResultIds(
                procedureOrderId: 5001,
                procedureReportId: 7001,
                procedureResultIds: [9001],
            ));

        $audit = self::createMock(LabPersistAuditWriter::class);
        $audit->expects(self::once())
            ->method('record')
            ->with(99, 42, [9001], 'completed');

        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->persist($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(201, $response->getStatusCode());

        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertSame(5001, $payload['procedure_order_id']);
        self::assertSame(7001, $payload['procedure_report_id']);
        self::assertSame([9001], $payload['procedure_result_ids']);
        self::assertSame(42, $payload['patient_id']);
        self::assertSame('completed', $payload['extraction_status']);
    }

    #[Test]
    public function returns201WithMultipleResultIdsForMultiValueExtraction(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $writer = self::createMock(LabResultWriter::class);
        $writer->method('persist')->willReturn(new LabResultIds(
            procedureOrderId: 5001,
            procedureReportId: 7001,
            procedureResultIds: [9001, 9002, 9003],
        ));

        $audit = self::createMock(LabPersistAuditWriter::class);
        $audit->expects(self::once())
            ->method('record')
            ->with(self::anything(), self::anything(), [9001, 9002, 9003], self::anything());

        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $body = $this->validBody();
        $body['values'] = [
            ['test_name' => 'Glucose', 'value' => '180'],
            ['test_name' => 'A1C', 'value' => '9.5', 'unit' => '%'],
            ['test_name' => 'Creatinine', 'value' => '1.0', 'unit' => 'mg/dL'],
        ];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);
        $response = $controller->persist($request);

        self::assertSame(201, $response->getStatusCode());
        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertSame([9001, 9002, 9003], $payload['procedure_result_ids']);
    }

    #[Test]
    public function reportsPartialStatusWhenUnsupportedFieldsPresent(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $writer = self::createMock(LabResultWriter::class);
        $writer->method('persist')->willReturn(new LabResultIds(5001, 7001, [9001]));

        $audit = self::createMock(LabPersistAuditWriter::class);
        $audit->expects(self::once())
            ->method('record')
            ->with(self::anything(), self::anything(), self::anything(), 'partial');

        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $body = $this->validBody();
        $body['unsupported_fields'] = ['urinalysis_specific_gravity'];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);
        $response = $controller->persist($request);

        self::assertSame(201, $response->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 500 — write failure
    // ---------------------------------------------------------------

    #[Test]
    public function returns500AndSkipsAuditWhenWriterThrows(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $writer = self::createMock(LabResultWriter::class);
        $writer->method('persist')->willThrowException(new \RuntimeException('db down'));

        $audit = $this->expectNeverCalledAudit();

        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $audit,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->persist($request);

        self::assertSame(500, $response->getStatusCode());
        self::assertStringNotContainsString('db down', (string) $response->getContent());
    }

    // ---------------------------------------------------------------
    // Test fixtures
    // ---------------------------------------------------------------

    private function makeController(
        ?DocumentOwnershipVerifier $verifier = null,
        ?LabResultWriter $writer = null,
        ?LabPersistAuditWriter $auditWriter = null,
    ): InternalLabPersistController {
        return new InternalLabPersistController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $verifier ?? self::createMock(DocumentOwnershipVerifier::class),
            $writer ?? self::createMock(LabResultWriter::class),
            $auditWriter ?? self::createMock(LabPersistAuditWriter::class),
        );
    }

    /**
     * @return LabResultWriter&MockObject
     */
    private function expectNeverCalledWriter(): LabResultWriter
    {
        $writer = self::createMock(LabResultWriter::class);
        $writer->expects(self::never())->method('persist');
        return $writer;
    }

    /**
     * @return LabPersistAuditWriter&MockObject
     */
    private function expectNeverCalledAudit(): LabPersistAuditWriter
    {
        $audit = self::createMock(LabPersistAuditWriter::class);
        $audit->expects(self::never())->method('record');
        return $audit;
    }

    /**
     * @param array<string, mixed>|null $body
     */
    private function makeRequest(
        string $token,
        ?array $body = null,
        ?string $bodyRaw = null,
    ): Request {
        $content = $bodyRaw ?? ($body !== null ? (string) json_encode($body) : '');
        $request = Request::create(
            '/agentforge/internal/persist_lab_result',
            'POST',
            [],
            [],
            [],
            ['CONTENT_TYPE' => 'application/json'],
            $content,
        );
        $request->headers->set('Authorization', "Bearer {$token}");
        return $request;
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

    /**
     * @return array<string, mixed>
     */
    private function validBody(): array
    {
        return [
            'document_id' => 123,
            'patient_id' => 42,
            'ordering_provider' => 'Dr. Smith',
            'accession_number' => 'ACC-1',
            'values' => [
                [
                    'test_name' => 'Glucose',
                    'value' => '180',
                    'unit' => 'mg/dL',
                    'abnormal_flag' => 'high',
                ],
            ],
            'extraction_confidence' => 0.92,
            'unsupported_fields' => [],
        ];
    }
}
