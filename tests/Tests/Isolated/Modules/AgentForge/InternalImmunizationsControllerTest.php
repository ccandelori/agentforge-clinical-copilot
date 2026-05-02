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

use Lcobucci\JWT\Validation\RequiredConstraintsViolated;
use OpenEMR\Modules\AgentForge\Controllers\InternalImmunizationsController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\ImmunizationsRepository;
use OpenEMR\Modules\AgentForge\Services\ValidatedClaims;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalImmunizationsController.
 *
 * Mirrors the allergies/encounters controller contract: JWT bearer
 * validation, pid query-param shape check, pid-mismatch refusal
 * (defense-in-depth on top of the shared-secret JWT validation), and a
 * 200 with an immunizations array on the happy path.
 */
final class InternalImmunizationsControllerTest extends TestCase
{
    #[Test]
    public function showReturns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = new InternalImmunizationsController(
            self::createMock(AgentJwtValidator::class),
            self::createMock(ImmunizationsRepository::class),
        );

        $response = $controller->show(Request::create('/x', 'GET'));

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401WhenJwtValidationFailsWithRuntimeException(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willThrowException(new RuntimeException('Token has expired'));

        $controller = new InternalImmunizationsController(
            $validator,
            self::createMock(ImmunizationsRepository::class),
        );

        $request = Request::create('/x', 'GET');
        $request->headers->set('Authorization', 'Bearer expired.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401WhenJwtValidationFailsWithLcobucciConstraintViolation(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willThrowException(new RequiredConstraintsViolated());

        $controller = new InternalImmunizationsController(
            $validator,
            self::createMock(ImmunizationsRepository::class),
        );

        $request = Request::create('/x', 'GET');
        $request->headers->set('Authorization', 'Bearer bad.signature.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns400WhenPidQueryParamIsMissing(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        $controller = new InternalImmunizationsController(
            $validator,
            self::createMock(ImmunizationsRepository::class),
        );

        $request = Request::create('/x', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_BAD_REQUEST, $response->getStatusCode());
    }

    #[Test]
    public function showReturns400WhenPidQueryParamIsZero(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        $controller = new InternalImmunizationsController(
            $validator,
            self::createMock(ImmunizationsRepository::class),
        );

        $request = Request::create('/x?pid=0', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_BAD_REQUEST, $response->getStatusCode());
    }

    #[Test]
    public function showReturns403WhenRequestedPidDoesNotMatchTokenClaim(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        // Repository must NOT be called when pids mismatch.
        $repository = self::createMock(ImmunizationsRepository::class);
        $repository->expects(self::never())->method('findByPid');

        $controller = new InternalImmunizationsController($validator, $repository);

        $request = Request::create('/x?pid=999', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_FORBIDDEN, $response->getStatusCode());
    }

    #[Test]
    public function showReturns200WithImmunizationsListForMatchingPid(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 8));

        $repository = self::createMock(ImmunizationsRepository::class);
        $repository->expects(self::once())
            ->method('findByPid')
            ->with(8)
            ->willReturn([
                [
                    'id' => 100,
                    'cvx_code' => '140',
                    'vaccine_name' => 'Influenza, seasonal, injectable, preservative free',
                    'administered_date' => '2025-07-11',
                    'manufacturer' => null,
                    'lot_number' => null,
                    'note' => null,
                ],
            ]);

        $controller = new InternalImmunizationsController($validator, $repository);

        $request = Request::create('/x?pid=8', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_OK, $response->getStatusCode());

        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        self::assertArrayHasKey('immunizations', $body);
        self::assertCount(1, $body['immunizations']);
        self::assertSame('140', $body['immunizations'][0]['cvx_code']);
    }

    #[Test]
    public function showReturns200WithEmptyImmunizationsListWhenRepositoryReturnsNothing(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        $repository = self::createMock(ImmunizationsRepository::class);
        $repository->method('findByPid')->willReturn([]);

        $controller = new InternalImmunizationsController($validator, $repository);

        $request = Request::create('/x?pid=123', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());

        $body = json_decode((string) $response->getContent(), true);
        self::assertSame(['immunizations' => []], $body);
    }
}
