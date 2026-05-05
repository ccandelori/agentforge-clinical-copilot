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
use OpenEMR\Modules\AgentForge\Controllers\InternalAllergiesController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\AllergiesRepository;
use OpenEMR\Modules\AgentForge\Services\ValidatedClaims;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalAllergiesController.
 *
 * Mirrors the medications/problems controller contract: JWT bearer
 * validation, pid query-param shape check, and pid-mismatch refusal
 * (defense-in-depth on top of the shared-secret JWT validation).
 */
final class InternalAllergiesControllerTest extends TestCase
{
    #[Test]
    public function showReturns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = new InternalAllergiesController(
            self::createMock(AgentJwtValidator::class),
            self::createMock(AllergiesRepository::class),
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

        $controller = new InternalAllergiesController(
            $validator,
            self::createMock(AllergiesRepository::class),
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

        $controller = new InternalAllergiesController(
            $validator,
            self::createMock(AllergiesRepository::class),
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

        $controller = new InternalAllergiesController(
            $validator,
            self::createMock(AllergiesRepository::class),
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

        $controller = new InternalAllergiesController(
            $validator,
            self::createMock(AllergiesRepository::class),
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
        $repository = self::createMock(AllergiesRepository::class);
        $repository->expects(self::never())->method('findActiveByPid');

        $controller = new InternalAllergiesController($validator, $repository);

        $request = Request::create('/x?pid=999', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_FORBIDDEN, $response->getStatusCode());
    }

    #[Test]
    public function showReturns200WithAllergiesListForMatchingPid(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        $repository = self::createMock(AllergiesRepository::class);
        $repository->expects(self::once())
            ->method('findActiveByPid')
            ->with(123)
            ->willReturn([
                [
                    'id' => 1,
                    'name' => 'Penicillin',
                    'reaction' => 'Rash',
                    'severity' => 'moderate',
                    'begin_date' => '2020-01-15',
                    'end_date' => null,
                ],
            ]);

        $controller = new InternalAllergiesController($validator, $repository);

        $request = Request::create('/x?pid=123', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_OK, $response->getStatusCode());

        /** @var array{allergies: list<array<string, mixed>>} $body */
        $body = json_decode((string) $response->getContent(), true);
        self::assertCount(1, $body['allergies']);
        self::assertSame('Penicillin', $body['allergies'][0]['name']);
    }

    #[Test]
    public function showReturns200WithEmptyAllergiesListWhenRepositoryReturnsNothing(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 123));

        $repository = self::createMock(AllergiesRepository::class);
        $repository->method('findActiveByPid')->willReturn([]);

        $controller = new InternalAllergiesController($validator, $repository);

        $request = Request::create('/x?pid=123', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());

        $body = json_decode((string) $response->getContent(), true);
        self::assertSame(['allergies' => []], $body);
    }
}
