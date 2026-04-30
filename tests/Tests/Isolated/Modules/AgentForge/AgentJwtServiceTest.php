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
use InvalidArgumentException;
use Lcobucci\Clock\FrozenClock;
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use Lcobucci\JWT\UnencryptedToken;
use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use OpenEMR\Modules\AgentForge\Services\BreakglassContext;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;
use RuntimeException;

/**
 * Behavior tests for AgentJwtService.
 *
 * Constructor validation (subtask 6.2), env-loading factory, and the
 * full mintToken contract (subtask 6.5) — claim shape, signature, TTL.
 * No DB dependencies; UserRoleLookup is mocked and the clock is frozen
 * so token contents are deterministic across runs.
 */
final class AgentJwtServiceTest extends TestCase
{
    private const TEST_SECRET = '0123456789abcdef0123456789abcdef';  // 32 bytes
    private const TEST_NOW = '2026-04-30T15:00:00+00:00';

    protected function setUp(): void
    {
        putenv('AGENTFORGE_JWT_SECRET=');
    }

    protected function tearDown(): void
    {
        putenv('AGENTFORGE_JWT_SECRET=');
    }

    #[Test]
    public function constructorAcceptsSecretOfAtLeast32Bytes(): void
    {
        $this->expectNotToPerformAssertions();
        $this->makeService();
    }

    #[Test]
    public function constructorRejectsSecretShorterThan32Bytes(): void
    {
        $this->expectException(InvalidArgumentException::class);
        $this->expectExceptionMessageMatches('/at least 32/');

        new AgentJwtService(
            secret: str_repeat('a', 31),
            roleLookup: self::createMock(UserRoleLookup::class),
            clock: $this->makeClock(),
        );
    }

    #[Test]
    public function constructorRejectsEmptySecret(): void
    {
        $this->expectException(InvalidArgumentException::class);

        new AgentJwtService(
            secret: '',
            roleLookup: self::createMock(UserRoleLookup::class),
            clock: $this->makeClock(),
        );
    }

    #[Test]
    public function fromEnvironmentReadsAgentForgeJwtSecret(): void
    {
        putenv('AGENTFORGE_JWT_SECRET=' . str_repeat('x', 64));

        $this->expectNotToPerformAssertions();
        AgentJwtService::fromEnvironment(
            self::createMock(UserRoleLookup::class),
            $this->makeClock(),
        );
    }

    #[Test]
    public function fromEnvironmentThrowsWhenSecretMissing(): void
    {
        $this->expectException(RuntimeException::class);
        $this->expectExceptionMessageMatches('/AGENTFORGE_JWT_SECRET/');

        AgentJwtService::fromEnvironment(
            self::createMock(UserRoleLookup::class),
            $this->makeClock(),
        );
    }

    #[Test]
    public function mintTokenReturnsParseableJwtWithExpectedClaims(): void
    {
        $service = $this->makeService(role: 'Physicians');

        $tokenString = $service->mintToken(
            userId: 42,
            username: 'jpatel',
            patientId: 123,
            breakglass: new BreakglassContext(flag: false),
        );

        $claims = $this->parseTokenClaims($tokenString);

        self::assertSame('openemr-agentforge', $claims['iss']);
        self::assertSame('42', $claims['sub']);
        self::assertSame('jpatel', $claims['username']);
        self::assertSame(123, $claims['patient_id']);
        self::assertSame('Physicians', $claims['role']);
        self::assertFalse($claims['breakglass_flag']);
        self::assertNull($claims['breakglass_reason']);
    }

    #[Test]
    public function mintTokenSetsExpirationFiveMinutesAfterIssuance(): void
    {
        $service = $this->makeService();

        $tokenString = $service->mintToken(
            userId: 1,
            username: 'admin',
            patientId: 1,
            breakglass: new BreakglassContext(flag: false),
        );

        $claims = $this->parseTokenClaims($tokenString);
        $iat = $claims['iat'];
        $exp = $claims['exp'];

        self::assertInstanceOf(DateTimeImmutable::class, $iat);
        self::assertInstanceOf(DateTimeImmutable::class, $exp);
        self::assertSame(300, $exp->getTimestamp() - $iat->getTimestamp());
    }

    #[Test]
    public function mintTokenIncludesBreakglassReasonWhenFlagIsSet(): void
    {
        $service = $this->makeService(role: 'Physicians');

        $tokenString = $service->mintToken(
            userId: 7,
            username: 'attending',
            patientId: 555,
            breakglass: new BreakglassContext(
                flag: true,
                reason: 'After-hours admit; PCP unreachable.'
            ),
        );

        $claims = $this->parseTokenClaims($tokenString);

        self::assertTrue($claims['breakglass_flag']);
        self::assertSame('After-hours admit; PCP unreachable.', $claims['breakglass_reason']);
    }

    #[Test]
    public function mintTokenSignsWithHs256SoVerificationFailsUnderDifferentSecret(): void
    {
        $service = $this->makeService();

        $tokenString = $service->mintToken(
            userId: 1,
            username: 'admin',
            patientId: 1,
            breakglass: new BreakglassContext(flag: false),
        );

        $wrongSecretConfig = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(str_repeat('z', 32))
        );
        $token = $wrongSecretConfig->parser()->parse($tokenString);
        $verified = $wrongSecretConfig->validator()->validate(
            $token,
            new \Lcobucci\JWT\Validation\Constraint\SignedWith(
                $wrongSecretConfig->signer(),
                $wrongSecretConfig->verificationKey(),
            )
        );

        self::assertFalse(
            $verified,
            'Token verified under wrong secret — HS256 signature is not enforcing key match.'
        );
    }

    #[Test]
    public function mintTokenRoleIsNullWhenUserHasNoGaclGroup(): void
    {
        $service = $this->makeService(role: null);

        $tokenString = $service->mintToken(
            userId: 99,
            username: 'newhire',
            patientId: 1,
            breakglass: new BreakglassContext(flag: false),
        );

        $claims = $this->parseTokenClaims($tokenString);
        self::assertNull(
            $claims['role'],
            'Missing role should pass null to the sidecar, not a default like "unknown".'
        );
    }

    private function makeService(?string $role = 'admin'): AgentJwtService
    {
        $roleLookup = self::createMock(UserRoleLookup::class);
        $roleLookup->method('findPrimaryGroup')->willReturn($role);

        return new AgentJwtService(
            secret: self::TEST_SECRET,
            roleLookup: $roleLookup,
            clock: $this->makeClock(),
        );
    }

    private function makeClock(): ClockInterface
    {
        return new FrozenClock(new DateTimeImmutable(self::TEST_NOW));
    }

    /**
     * @return array<string, mixed>
     */
    private function parseTokenClaims(string $tokenString): array
    {
        $config = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::TEST_SECRET),
        );
        $token = $config->parser()->parse($tokenString);
        // Parser returns Token (the base interface); HS256 always yields
        // an UnencryptedToken in practice, but phpstan needs the narrow.
        self::assertInstanceOf(UnencryptedToken::class, $token);

        return $token->claims()->all();
    }
}
