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
use OpenEMR\Modules\AgentForge\Controllers\InternalIntakePromoteController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\IntakePromoteAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakePromotionWriter;
use OpenEMR\Modules\AgentForge\Services\PromotedItemHandle;
use OpenEMR\Modules\AgentForge\Services\PromotionItem;
use OpenEMR\Modules\AgentForge\Services\PromotionResult;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\MockObject\MockObject;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;

/**
 * Behavior tests for InternalIntakePromoteController (Gap 2).
 *
 * Coverage:
 *
 *   1. Auth — missing/malformed/wrong-scheme/wrong-signature → 401
 *   2. Body validation — empty/non-JSON/wrong-shape/missing fields → 400
 *   3. Scope — JWT.patientId != body.patient_id → 403
 *   4. Items validation — empty list, oversize batch, invalid kind,
 *      empty title → 400
 *   5. Happy path — writer + audit fire with correct args, 201
 *   6. Writer throws → 500, audit skipped
 *
 * Username lookup is mocked at the Connection layer because the
 * controller queries the users table directly (avoiding a separate
 * UsernameLookup service for now to keep the wire surface minimal).
 */
final class InternalIntakePromoteControllerTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';
    private const TEST_NOW = '2026-05-09T15:00:00+00:00';

    // ---------------------------------------------------------------
    // 401 — JWT validation failures
    // ---------------------------------------------------------------

    #[Test]
    public function returns401WhenAuthorizationHeaderMissing(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = Request::create('/agentforge/internal/promote_intake', 'POST');

        self::assertSame(401, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns401WhenBearerMalformed(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = $this->makeRequest(token: 'not.a.real.jwt', body: $this->validBody());

        self::assertSame(401, $controller->promote($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 400 — body validation
    // ---------------------------------------------------------------

    #[Test]
    public function returns400WhenBodyEmpty(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), bodyRaw: '');

        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBodyIsInvalidJson(): void
    {
        $controller = $this->makeController();
        $request = $this->makeRequest(
            token: $this->mintToken(42, 99),
            bodyRaw: '{not valid json',
        );
        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenPatientIdMissing(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        unset($body['patient_id']);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenItemsListEmpty(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['items'] = [];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenItemKindIsInvalid(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['items'] = [['kind' => 'lab_result', 'title' => 'Glucose']];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        $response = $controller->promote($request);
        self::assertSame(400, $response->getStatusCode());
        // The error mentions the allowed kinds so the caller can self-correct.
        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertArrayHasKey('allowed_kinds', $payload);
    }

    #[Test]
    public function returns400WhenItemTitleEmpty(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['items'] = [['kind' => 'allergy', 'title' => '   ']];
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    #[Test]
    public function returns400WhenBatchExceedsMaxItems(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['items'] = array_fill(0, 101, ['kind' => 'allergy', 'title' => 'x']);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(400, $controller->promote($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 403 — scope check
    // ---------------------------------------------------------------

    #[Test]
    public function returns403WhenJwtPatientIdDoesNotMatchBodyPatientId(): void
    {
        $writer = $this->expectNeverCalledWriter();
        $audit = $this->expectNeverCalledAudit();
        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $body = $this->validBody();
        $body['patient_id'] = 999;  // JWT below carries patient_id=42.
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $body);

        self::assertSame(403, $controller->promote($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 201 — happy path
    // ---------------------------------------------------------------

    #[Test]
    public function happyPathInvokesWriterAndAuditAndReturns201(): void
    {
        $writer = self::createMock(IntakePromotionWriter::class);
        $writer->expects(self::once())
            ->method('persist')
            ->with(
                self::equalTo(42),                             // patientId
                self::equalTo('admin'),                        // username (looked up)
                self::equalTo('qr-uuid-1'),                    // questionnaireResponseId
                self::equalTo(777),                            // documentId
                self::callback(function (array $items): bool {
                    return count($items) === 2
                        && $items[0] instanceof PromotionItem
                        && $items[0]->kind === 'allergy'
                        && $items[0]->title === 'Penicillin'
                        && $items[0]->details === 'rash'
                        && $items[1]->kind === 'medical_problem'
                        && $items[1]->title === 'Type 2 diabetes';
                }),
            )
            ->willReturn(new PromotionResult([
                new PromotedItemHandle(kind: 'allergy', listsId: 4001, title: 'Penicillin'),
                new PromotedItemHandle(kind: 'medical_problem', listsId: 4002, title: 'Type 2 diabetes'),
            ]));

        $audit = self::createMock(IntakePromoteAuditWriter::class);
        $audit->expects(self::once())
            ->method('record')
            ->with(99, 42, 'qr-uuid-1', 2);

        $controller = $this->makeController(
            writer: $writer,
            auditWriter: $audit,
            usernameForUserId99: 'admin',
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->promote($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(201, $response->getStatusCode());

        $payload = json_decode((string) $response->getContent(), true);
        self::assertIsArray($payload);
        self::assertSame(2, $payload['count']);
        self::assertCount(2, $payload['promoted']);
        self::assertSame('allergy', $payload['promoted'][0]['kind']);
        self::assertSame(4001, $payload['promoted'][0]['lists_id']);
    }

    #[Test]
    public function fallsBackToUserIdStringWhenUsernameLookupReturnsNothing(): void
    {
        $writer = self::createMock(IntakePromotionWriter::class);
        $writer->expects(self::once())
            ->method('persist')
            ->with(
                self::anything(),
                // No row in `users` for id=99 → fallback to "user-99".
                self::equalTo('user-99'),
                self::anything(),
                self::anything(),
                self::anything(),
            )
            ->willReturn(new PromotionResult([
                new PromotedItemHandle(kind: 'allergy', listsId: 1, title: 'x'),
            ]));

        $controller = $this->makeController(
            writer: $writer,
            auditWriter: self::createMock(IntakePromoteAuditWriter::class),
            usernameForUserId99: false,  // Simulate "no row".
        );

        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        self::assertSame(201, $controller->promote($request)->getStatusCode());
    }

    // ---------------------------------------------------------------
    // 500 — write failure
    // ---------------------------------------------------------------

    #[Test]
    public function returns500AndSkipsAuditWhenWriterThrows(): void
    {
        $writer = self::createMock(IntakePromotionWriter::class);
        $writer->method('persist')->willThrowException(new \RuntimeException('db down'));

        $audit = $this->expectNeverCalledAudit();

        $controller = $this->makeController(writer: $writer, auditWriter: $audit);
        $request = $this->makeRequest(token: $this->mintToken(42, 99), body: $this->validBody());
        $response = $controller->promote($request);

        self::assertSame(500, $response->getStatusCode());
        // The DB error message must not leak into the response.
        self::assertStringNotContainsString('db down', (string) $response->getContent());
    }

    // ---------------------------------------------------------------
    // Test fixtures
    // ---------------------------------------------------------------

    private function makeController(
        ?IntakePromotionWriter $writer = null,
        ?IntakePromoteAuditWriter $auditWriter = null,
        string|false $usernameForUserId99 = 'admin',
    ): InternalIntakePromoteController {
        $connection = self::createMock(Connection::class);
        // Username lookup. ``fetchOne`` returns the first column of
        // the first row (string|false). We answer for userId=99
        // (the only id our test JWTs carry).
        $connection->method('fetchOne')->willReturn($usernameForUserId99);

        return new InternalIntakePromoteController(
            new AgentJwtValidator(
                self::TEST_SECRET,
                new FrozenClock(new DateTimeImmutable(self::TEST_NOW)),
            ),
            $connection,
            $writer ?? self::createMock(IntakePromotionWriter::class),
            $auditWriter ?? self::createMock(IntakePromoteAuditWriter::class),
        );
    }

    /**
     * @return IntakePromotionWriter&MockObject
     */
    private function expectNeverCalledWriter(): IntakePromotionWriter
    {
        $writer = self::createMock(IntakePromotionWriter::class);
        $writer->expects(self::never())->method('persist');
        return $writer;
    }

    /**
     * @return IntakePromoteAuditWriter&MockObject
     */
    private function expectNeverCalledAudit(): IntakePromoteAuditWriter
    {
        $audit = self::createMock(IntakePromoteAuditWriter::class);
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
            '/agentforge/internal/promote_intake',
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
            'patient_id' => 42,
            'questionnaire_response_id' => 'qr-uuid-1',
            'document_id' => 777,
            'items' => [
                ['kind' => 'allergy', 'title' => 'Penicillin', 'details' => 'rash'],
                ['kind' => 'medical_problem', 'title' => 'Type 2 diabetes'],
            ],
        ];
    }
}
