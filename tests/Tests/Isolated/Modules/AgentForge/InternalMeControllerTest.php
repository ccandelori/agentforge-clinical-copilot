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
use OpenEMR\Modules\AgentForge\Controllers\InternalMeController;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\UserIdentity;
use OpenEMR\Modules\AgentForge\Services\UserIdentityRepository;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Behavior tests for InternalMeController — the OpenEMR-side endpoint
 * the dashboard auth bridge calls to resolve OIDC user UUID into the
 * integer user_id + username + GACL role the internal JWT requires.
 *
 * Auth model: the request carries a "lookup-purpose" JWT signed with
 * AGENTFORGE_JWT_SECRET. The validator confirms signature, issuer, and
 * expiration but does NOT require user/patient claims (we don't have
 * them yet — the lookup is what bootstraps them). See ADR-0001.
 */
final class InternalMeControllerTest extends TestCase
{
    private function makeController(
        ?AgentJwtValidator $validator = null,
        ?UserIdentityRepository $identities = null,
        ?UserRoleLookup $roles = null,
    ): InternalMeController {
        return new InternalMeController(
            $validator ?? self::createMock(AgentJwtValidator::class),
            $identities ?? self::createMock(UserIdentityRepository::class),
            $roles ?? self::createMock(UserRoleLookup::class),
        );
    }

    #[Test]
    public function showReturns401WhenAuthorizationHeaderMissing(): void
    {
        $controller = $this->makeController();
        $response = $controller->show(Request::create('/me', 'GET'));

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401WhenLookupJwtFailsRuntimeValidation(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateLookupBearer')
            ->willThrowException(new RuntimeException('Token has expired'));

        $controller = $this->makeController(validator: $validator);

        $request = Request::create('/me', 'GET', ['user_uuid' => 'whatever']);
        $request->headers->set('Authorization', 'Bearer expired.token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns401OnLcobucciConstraintViolation(): void
    {
        $validator = self::createMock(AgentJwtValidator::class);
        $validator->method('validateLookupBearer')
            ->willThrowException(new RequiredConstraintsViolated());

        $controller = $this->makeController(validator: $validator);

        $request = Request::create('/me', 'GET', ['user_uuid' => 'whatever']);
        $request->headers->set('Authorization', 'Bearer bad.signature');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_UNAUTHORIZED, $response->getStatusCode());
    }

    #[Test]
    public function showReturns400WhenUserUuidQueryParamIsMissing(): void
    {
        $controller = $this->makeController();

        $request = Request::create('/me', 'GET');
        $request->headers->set('Authorization', 'Bearer good.token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_BAD_REQUEST, $response->getStatusCode());
    }

    #[Test]
    public function showReturns400WhenUserUuidIsEmpty(): void
    {
        $controller = $this->makeController();

        $request = Request::create('/me', 'GET', ['user_uuid' => '']);
        $request->headers->set('Authorization', 'Bearer good.token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_BAD_REQUEST, $response->getStatusCode());
    }

    #[Test]
    public function showReturns404WhenIdentityRepositoryReturnsNull(): void
    {
        $identities = self::createMock(UserIdentityRepository::class);
        $identities->method('findByUuid')->willReturn(null);

        $controller = $this->makeController(identities: $identities);

        $request = Request::create('/me', 'GET', ['user_uuid' => 'unknown-uuid']);
        $request->headers->set('Authorization', 'Bearer good.token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_NOT_FOUND, $response->getStatusCode());
    }

    #[Test]
    public function showReturnsResolvedIdentityWithRoleOn200(): void
    {
        $identities = self::createMock(UserIdentityRepository::class);
        $identities->expects(self::once())
            ->method('findByUuid')
            ->with('8a7b-uuid')
            ->willReturn(new UserIdentity(userId: 17, username: 'admin'));

        $roles = self::createMock(UserRoleLookup::class);
        $roles->expects(self::once())
            ->method('findPrimaryGroup')
            ->with('admin')
            ->willReturn('Administrators');

        $controller = $this->makeController(identities: $identities, roles: $roles);

        $request = Request::create('/me', 'GET', ['user_uuid' => '8a7b-uuid']);
        $request->headers->set('Authorization', 'Bearer good.token');

        $response = $controller->show($request);

        self::assertInstanceOf(JsonResponse::class, $response);
        self::assertSame(Response::HTTP_OK, $response->getStatusCode());
        $body = json_decode((string) $response->getContent(), true);
        self::assertSame([
            'user_id' => 17,
            'username' => 'admin',
            'role' => 'Administrators',
        ], $body);
    }

    #[Test]
    public function showReturnsRoleNullWhenUserHasNoGaclGroup(): void
    {
        $identities = self::createMock(UserIdentityRepository::class);
        $identities->method('findByUuid')->willReturn(
            new UserIdentity(userId: 22, username: 'orphan'),
        );

        $roles = self::createMock(UserRoleLookup::class);
        $roles->method('findPrimaryGroup')->willReturn(null);

        $controller = $this->makeController(identities: $identities, roles: $roles);

        $request = Request::create('/me', 'GET', ['user_uuid' => 'orphan-uuid']);
        $request->headers->set('Authorization', 'Bearer good.token');

        $response = $controller->show($request);

        self::assertSame(Response::HTTP_OK, $response->getStatusCode());
        $body = json_decode((string) $response->getContent(), true);
        self::assertSame([
            'user_id' => 22,
            'username' => 'orphan',
            'role' => null,
        ], $body);
    }
}
