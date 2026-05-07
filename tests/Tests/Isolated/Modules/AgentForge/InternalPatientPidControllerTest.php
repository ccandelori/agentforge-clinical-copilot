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

use OpenEMR\Modules\AgentForge\Controllers\InternalPatientPidController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behaviour tests for InternalPatientPidController — the second
 * identity-bootstrap endpoint of the dashboard auth bridge
 * (ADR-0001). Pairs with /me: the /patient_pid endpoint resolves a
 * FHIR Patient UUID into the integer pid the agent's JWT carries.
 *
 * Auth model is identical to /me: a "lookup-purpose" JWT signed with
 * AGENTFORGE_JWT_SECRET, signature + issuer + expiration only.
 */
final class InternalPatientPidControllerTest extends TestCase
{
    private function makeController(
        ?AgentJwtValidator $validator = null,
        ?PatientPidRepository $repo = null,
    ): InternalPatientPidController {
        return new InternalPatientPidController(
            $validator ?? self::createMock(AgentJwtValidator::class),
            $repo ?? self::createMock(PatientPidRepository::class),
        );
    }

    #[Test]
    public function showReturns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $response = $controller->show(Request::create('/patient_pid', 'GET'));

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401WhenLookupJwtIsInvalid(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateLookupBearer')
            ->willThrowException(new RuntimeException('expired'));

        $controller = $this->makeController(validator: $validator);

        $request = Request::create('/patient_pid', 'GET', ['patient_uuid' => 'abc']);
        $request->headers->set('Authorization', 'Bearer expired.token');

        self::assertSame(
            Response::HTTP_UNAUTHORIZED,
            $controller->show($request)->getStatusCode(),
        );
    }

    #[Test]
    public function showReturns400WhenPatientUuidIsMissing(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/patient_pid', 'GET');
        $request->headers->set('Authorization', 'Bearer good');

        self::assertSame(
            Response::HTTP_BAD_REQUEST,
            $controller->show($request)->getStatusCode(),
        );
    }

    #[Test]
    public function showReturns400WhenPatientUuidIsEmpty(): void
    {
        $controller = $this->makeController();
        $request = Request::create('/patient_pid', 'GET', ['patient_uuid' => '']);
        $request->headers->set('Authorization', 'Bearer good');

        self::assertSame(
            Response::HTTP_BAD_REQUEST,
            $controller->show($request)->getStatusCode(),
        );
    }

    #[Test]
    public function showReturns404WhenRepositoryHasNoPidForTheUuid(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->method('findPidByUuid')->willReturn(null);

        $controller = $this->makeController(repo: $repo);

        $request = Request::create('/patient_pid', 'GET', ['patient_uuid' => 'unknown']);
        $request->headers->set('Authorization', 'Bearer good');

        self::assertSame(
            Response::HTTP_NOT_FOUND,
            $controller->show($request)->getStatusCode(),
        );
    }

    #[Test]
    public function showReturnsResolvedPidOn200(): void
    {
        $repo = self::createMock(PatientPidRepository::class);
        $repo->expects(self::once())
            ->method('findPidByUuid')
            ->with('abc-uuid')
            ->willReturn(42);

        $controller = $this->makeController(repo: $repo);

        $request = Request::create('/patient_pid', 'GET', ['patient_uuid' => 'abc-uuid']);
        $request->headers->set('Authorization', 'Bearer good');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());
        $body = json_decode((string) $response->getContent(), true);
        self::assertSame(['pid' => 42], $body);
    }
}
