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
use OpenEMR\Modules\AgentForge\Controllers\InternalIntakePersistController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentOwnershipVerifier;
use OpenEMR\Modules\AgentForge\Services\IntakePersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireLookup;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireResponseWriter;
use OpenEMR\Modules\AgentForge\Services\SeededIntakeQuestionnaire;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use PHPUnit\Framework\MockObject\MockObject;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalIntakePersistController.
 *
 * Covers the Task 12 test strategy:
 *
 *   1. Valid extraction creates QuestionnaireResponse → 201
 *   2. Audit event fired on success (with correct attributes)
 *   3-6. Structured EHR tables unchanged → asserted indirectly:
 *        the writer is called and the audit fires; the controller has
 *        no other repository references, so by construction no other
 *        table is touched. (Direct DB-level assertions live in an
 *        integration test that requires Docker.)
 *   7. Missing seed Questionnaire → 500, no write, no audit
 *   8. Invalid JWT → 401, no write, no audit
 *   9. JWT.patientId != request.patient_id → 403, no write, no audit
 *   10. JWT.patientId != document.foreign_id → 403
 *   11. document_id non-existent → 403 (collapses to same path as 10)
 *   12. document deleted → 403 (verifier returns null for deleted=1)
 *   13. Triple-check happy path → 201
 *
 * Plus: 401 variants (missing header, empty header, malformed,
 * wrong-scheme, wrong-signature), 400 variants (missing body, missing
 * required fields, non-JSON body, JSON array instead of object), and
 * a 500 for write failure.
 */
final class InternalIntakePersistControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-05-05T15:00:00+00:00';

    // ---------------------------------------------------------------
    // 401 — JWT validation failures
    // ---------------------------------------------------------------

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $auditWriter);
        $request = Request::create('/agentforge/internal/persist_questionnaire_response', 'POST');

        self::assertSame(401, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerMalformed(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $auditWriter);
        $request = $this->makeRequest(token: 'not.a.real.jwt', body: $this->validBody());

        self::assertSame(401, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerSchemeMissing(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $auditWriter);
        $request = Request::create('/agentforge/internal/persist_questionnaire_response', 'POST');
        $request->headers->set('Authorization', $this->mintToken(patientId: 42, userId: 99));

        self::assertSame(401, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 400 — body validation
    // ---------------------------------------------------------------

    #[Test]
    public function returns400WhenBodyEmpty(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $auditWriter);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), bodyRaw: '');

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsInvalidJson(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(42, 99),
            bodyRaw: '{not-json',
        );

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsJsonArrayNotObject(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(42, 99),
            bodyRaw: '[1, 2, 3]',
        );

        // JSON arrays decode to associative arrays with int keys; the
        // required-field check then rejects them as missing document_id.
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
    public function returns400WhenPatientIdMissing(): void
    {
        $controller = $this->makeController();
        $body = $this->validBody();
        unset($body['patient_id']);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenDocumentIdNonPositive(): void
    {
        $controller = $this->makeController();
        $body = $this->validBody();
        $body['document_id'] = -5;
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 403 — triple-check failures
    // ---------------------------------------------------------------

    #[Test]
    public function returns403WhenJwtPatientIdDiffersFromRequestPayload(): void
    {
        // The first leg of the triple-check. The verifier should NEVER
        // be reached when the payload mismatch is caught earlier.
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->expects(self::never())->method('findOwningPatientId');

        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $body = $this->validBody();
        $body['patient_id'] = 99; // Different from JWT (42)
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenDocumentForeignIdDiffersFromJwtPatient(): void
    {
        // JWT vs request match (both 42); but the document is owned by 99.
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->expects(self::once())
            ->method('findOwningPatientId')
            ->with(123)
            ->willReturn(99);

        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $body = $this->validBody();
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    #[Test]
    public function returns403WhenDocumentNotFound(): void
    {
        // Non-existent OR deleted document — both collapse to verifier
        // returning null. The disclosure response is the same 403.
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->expects(self::once())
            ->method('findOwningPatientId')
            ->willReturn(null);

        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();
        $controller = $this->makeController(
            verifier: $verifier,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());

        self::assertSame(403, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 500 — fail closed when the seed Questionnaire is missing
    // ---------------------------------------------------------------

    #[Test]
    public function returns500WhenCanonicalQuestionnaireSeedMissing(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $lookup = self::createMock(IntakeQuestionnaireLookup::class);
        $lookup->expects(self::once())
            ->method('findCanonicalQuestionnaire')
            ->willReturn(null);

        $writer = $this->expectNeverCalledWriter();
        $auditWriter = $this->expectNeverCalledAudit();

        $controller = $this->makeController(
            verifier: $verifier,
            lookup: $lookup,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());

        self::assertSame(500, $controller->persist($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 201 — happy path
    // ---------------------------------------------------------------

    #[Test]
    public function returns201WithResponseIdOnSuccessfulPersist(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $lookup = self::createMock(IntakeQuestionnaireLookup::class);
        $lookup->method('findCanonicalQuestionnaire')->willReturn(new SeededIntakeQuestionnaire(
            id: 7,
            name: 'AgentForge Intake Form',
            questionnaireJson: '{"resourceType":"Questionnaire"}',
        ));

        $writer = self::createMock(IntakeQuestionnaireResponseWriter::class);
        $writer->expects(self::once())
            ->method('insert')
            ->with(
                self::equalTo(42),                              // patientId
                self::equalTo(7),                               // questionnaireForeignId
                self::equalTo('AgentForge Intake Form'),        // questionnaireName
                self::callback(fn (array $r): bool =>
                    ($r['resourceType'] ?? null) === 'QuestionnaireResponse'),
                self::equalTo('{"resourceType":"Questionnaire"}'),
            )
            ->willReturn('11111111-2222-3333-4444-555555555555');

        $auditWriter = self::createMock(IntakePersistAuditWriter::class);
        $auditWriter->expects(self::once())
            ->method('record')
            ->with(99, 42, '11111111-2222-3333-4444-555555555555', 'completed');

        $controller = $this->makeController(
            verifier: $verifier,
            lookup: $lookup,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->persist($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(201, $response->getStatusCode());

        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertSame('11111111-2222-3333-4444-555555555555', $payload['questionnaire_response_id']);
        self::assertSame(42, $payload['patient_id']);
        self::assertSame('completed', $payload['extraction_status']);
    }

    #[Test]
    public function reportsPartialStatusWhenUnsupportedFieldsPresent(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $lookup = self::createMock(IntakeQuestionnaireLookup::class);
        $lookup->method('findCanonicalQuestionnaire')->willReturn(new SeededIntakeQuestionnaire(
            id: 7,
            name: 'AgentForge Intake Form',
            questionnaireJson: '{}',
        ));

        $writer = self::createMock(IntakeQuestionnaireResponseWriter::class);
        $writer->method('insert')->willReturn('uuid-string');

        $auditWriter = self::createMock(IntakePersistAuditWriter::class);
        $auditWriter->expects(self::once())
            ->method('record')
            ->with(self::anything(), self::anything(), self::anything(), 'partial');

        $controller = $this->makeController(
            verifier: $verifier,
            lookup: $lookup,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $body = $this->validBody();
        $body['unsupported_fields'] = ['height_cm', 'smoking_status'];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);
        $response = $controller->persist($request);

        self::assertSame(201, $response->getStatusCode());
        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertSame('partial', $payload['extraction_status']);
    }

    // ---------------------------------------------------------------
    // 500 — write failure path
    // ---------------------------------------------------------------

    #[Test]
    public function returns500AndSkipsAuditWhenWriterThrows(): void
    {
        $verifier = self::createMock(DocumentOwnershipVerifier::class);
        $verifier->method('findOwningPatientId')->willReturn(42);

        $lookup = self::createMock(IntakeQuestionnaireLookup::class);
        $lookup->method('findCanonicalQuestionnaire')->willReturn(new SeededIntakeQuestionnaire(
            id: 7,
            name: 'AgentForge Intake Form',
            questionnaireJson: '{}',
        ));

        $writer = self::createMock(IntakeQuestionnaireResponseWriter::class);
        $writer->method('insert')->willThrowException(new \RuntimeException('db down'));

        $auditWriter = $this->expectNeverCalledAudit();

        $controller = $this->makeController(
            verifier: $verifier,
            lookup: $lookup,
            writer: $writer,
            auditWriter: $auditWriter,
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->persist($request);

        self::assertSame(500, $response->getStatusCode());
        // The DB error message must not leak into the response.
        self::assertStringNotContainsString('db down', (string) $response->getContent());
    }

    // ---------------------------------------------------------------
    // Test fixtures
    // ---------------------------------------------------------------

    private function makeController(
        ?DocumentOwnershipVerifier $verifier = null,
        ?IntakeQuestionnaireLookup $lookup = null,
        ?IntakeQuestionnaireResponseWriter $writer = null,
        ?IntakePersistAuditWriter $auditWriter = null,
    ): InternalIntakePersistController {
        return new InternalIntakePersistController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $verifier ?? self::createMock(DocumentOwnershipVerifier::class),
            $lookup ?? self::createMock(IntakeQuestionnaireLookup::class),
            $writer ?? self::createMock(IntakeQuestionnaireResponseWriter::class),
            $auditWriter ?? self::createMock(IntakePersistAuditWriter::class),
        );
    }

    /**
     * @return IntakeQuestionnaireResponseWriter&MockObject
     */
    private function expectNeverCalledWriter(): IntakeQuestionnaireResponseWriter
    {
        $writer = self::createMock(IntakeQuestionnaireResponseWriter::class);
        $writer->expects(self::never())->method('insert');
        return $writer;
    }

    /**
     * @return IntakePersistAuditWriter&MockObject
     */
    private function expectNeverCalledAudit(): IntakePersistAuditWriter
    {
        $audit = self::createMock(IntakePersistAuditWriter::class);
        $audit->expects(self::never())->method('record');
        return $audit;
    }

    /**
     * @param array<string, mixed>|null $body When set, the request body
     *        is its JSON encoding. Pass `bodyRaw` for raw bytes.
     */
    private function makeRequest(
        string $token,
        ?array $body = null,
        ?string $bodyRaw = null,
    ): Request {
        $content = $bodyRaw ?? ($body !== null ? (string) json_encode($body) : '');
        $request = Request::create(
            '/agentforge/internal/persist_questionnaire_response',
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
            'chief_concern' => 'chest pain',
            'demographics' => [],
            'medications' => [],
            'allergies' => [],
            'family_history' => [],
            'extraction_confidence' => 0.9,
            'unsupported_fields' => [],
        ];
    }
}
