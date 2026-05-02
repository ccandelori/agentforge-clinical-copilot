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
use OpenEMR\Modules\AgentForge\Controllers\InternalProceduresController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\ProceduresRepository;
use OpenEMR\Modules\AgentForge\Services\ValidatedClaims;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalProceduresController.
 *
 * Mirrors InternalLabsController's contract: JWT bearer + pid scope +
 * since_days clamping. Defaults are larger here (365 vs 90) because
 * procedures are typically annual or rarer.
 */
final class InternalProceduresControllerTest extends TestCase
{
    #[Test]
    public function showReturns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = new InternalProceduresController(
            self::createMock(AgentJwtValidator::class),
            self::createMock(ProceduresRepository::class),
        );

        $response = $controller->show(Request::create('/x', 'GET'));

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401WhenJwtValidationFailsWithRuntimeException(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willThrowException(new RuntimeException('Token has expired'));

        $controller = new InternalProceduresController(
            $validator,
            self::createMock(ProceduresRepository::class),
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

        $controller = new InternalProceduresController(
            $validator,
            self::createMock(ProceduresRepository::class),
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

        $controller = new InternalProceduresController(
            $validator,
            self::createMock(ProceduresRepository::class),
        );

        $request = Request::create('/x', 'GET');
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

        $repository = self::createMock(ProceduresRepository::class);
        $repository->expects(self::never())->method('findRecentByPid');

        $controller = new InternalProceduresController($validator, $repository);

        $request = Request::create('/x?pid=999', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_FORBIDDEN, $response->getStatusCode());
    }

    #[Test]
    public function showCallsRepositoryWithDefaultSinceDaysWhenOmitted(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 8));

        $repository = self::createMock(ProceduresRepository::class);
        $repository->expects(self::once())
            ->method('findRecentByPid')
            ->with(8, InternalProceduresController::DEFAULT_SINCE_DAYS)
            ->willReturn([]);

        $controller = new InternalProceduresController($validator, $repository);

        $request = Request::create('/x?pid=8', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());
    }

    #[Test]
    public function showClampsSinceDaysToServerSideMaximum(): void
    {
        // Defense in depth: a misbehaving model can't request the
        // patient's entire procedure history.
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 8));

        $repository = self::createMock(ProceduresRepository::class);
        $repository->expects(self::once())
            ->method('findRecentByPid')
            ->with(8, InternalProceduresController::MAX_SINCE_DAYS)
            ->willReturn([]);

        $controller = new InternalProceduresController($validator, $repository);

        $request = Request::create('/x?pid=8&since_days=99999', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $controller->show($request);
    }

    #[Test]
    public function showRevertsToDefaultWhenSinceDaysBelowMinimum(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 8));

        $repository = self::createMock(ProceduresRepository::class);
        $repository->expects(self::once())
            ->method('findRecentByPid')
            ->with(8, InternalProceduresController::DEFAULT_SINCE_DAYS)
            ->willReturn([]);

        $controller = new InternalProceduresController($validator, $repository);

        $request = Request::create('/x?pid=8&since_days=0', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $controller->show($request);
    }

    #[Test]
    public function showReturns200WithProceduresListForMatchingPid(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateBearer')
            ->willReturn(new ValidatedClaims(userId: 1, patientId: 8));

        $repository = self::createMock(ProceduresRepository::class);
        $repository->method('findRecentByPid')->willReturn([
            [
                'id' => 4321,
                'procedure_code' => 'SNOMED CT:171207006',
                'procedure_name' => 'Depression screening (procedure)',
                'date_ordered' => '2026-03-06',
                'status' => 'completed',
                'encounter_id' => 78,
            ],
        ]);

        $controller = new InternalProceduresController($validator, $repository);

        $request = Request::create('/x?pid=8', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token.here');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_OK, $response->getStatusCode());

        $body = json_decode((string) $response->getContent(), true);
        self::assertIsArray($body);
        self::assertArrayHasKey('procedures', $body);
        self::assertCount(1, $body['procedures']);
        self::assertSame('Depression screening (procedure)', $body['procedures'][0]['procedure_name']);
    }
}
