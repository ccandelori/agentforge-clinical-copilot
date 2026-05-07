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
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Psr\Clock\ClockInterface;
use RuntimeException;

/**
 * Unit tests for AgentJwtValidator::validateLookupBearer.
 *
 * The dashboard auth bridge (ADR-0001) needs to bootstrap a session's
 * OpenEMR identity *before* it can mint a "real" internal JWT carrying
 * `user_id` + `patient_id`. The lookup variant accepts a JWT signed
 * with the shared secret + correct issuer + non-expired, but does NOT
 * require any user-scoped claims — by construction we don't have them
 * yet at the moment of the lookup.
 */
final class AgentJwtValidatorLookupTest extends TestCase
{
    private const SECRET = 'a-very-long-test-secret-that-is-at-least-32b';

    private function makeValidator(?DateTimeImmutable $now = null): AgentJwtValidator
    {
        $clock = self::createMock(ClockInterface::class);
        $clock->method('now')->willReturn(
            $now ?? new DateTimeImmutable('2026-05-06T12:00:00+00:00'),
        );
        return new AgentJwtValidator(self::SECRET, $clock);
    }

    /**
     * Build a minimal lookup JWT — issuer set, signed, with `iat`/`exp`,
     * but no `user_id` (`sub`) or `patient_id`. Everything below the
     * surface uses the same Lcobucci config the validator does.
     */
    private function mintLookupToken(
        string $issuer = 'openemr-agentforge',
        ?DateTimeImmutable $iat = null,
        ?DateTimeImmutable $exp = null,
    ): string {
        $cfg = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText(self::SECRET),
        );
        $now = $iat ?? new DateTimeImmutable('2026-05-06T12:00:00+00:00');
        $expiresAt = $exp ?? $now->modify('+5 minutes');
        $token = $cfg->builder()
            ->issuedBy($issuer)
            ->issuedAt($now)
            ->expiresAt($expiresAt)
            ->getToken($cfg->signer(), $cfg->signingKey());
        return $token->toString();
    }

    #[Test]
    public function validateLookupBearerAcceptsTokenWithoutUserOrPatientClaims(): void
    {
        $sut = $this->makeValidator();

        $token = $this->mintLookupToken();

        // Should not throw — return is void.
        $sut->validateLookupBearer('Bearer ' . $token);
        $this->expectNotToPerformAssertions();
    }

    #[Test]
    public function validateLookupBearerThrowsWhenSchemeIsNotBearer(): void
    {
        $sut = $this->makeValidator();

        $this->expectException(RuntimeException::class);
        $sut->validateLookupBearer('Basic some-base64==');
    }

    #[Test]
    public function validateLookupBearerThrowsOnExpiredToken(): void
    {
        $now = new DateTimeImmutable('2026-05-06T12:00:00+00:00');
        $sut = $this->makeValidator($now);

        $expired = $this->mintLookupToken(
            iat: $now->modify('-10 minutes'),
            exp: $now->modify('-1 minute'),
        );

        $this->expectException(RuntimeException::class);
        $sut->validateLookupBearer('Bearer ' . $expired);
    }

    #[Test]
    public function validateLookupBearerThrowsOnWrongIssuer(): void
    {
        $sut = $this->makeValidator();

        $token = $this->mintLookupToken(issuer: 'someone-else');

        $this->expectException(\Lcobucci\JWT\Validation\RequiredConstraintsViolated::class);
        $sut->validateLookupBearer('Bearer ' . $token);
    }

    #[Test]
    public function validateLookupBearerThrowsOnUnsignedToken(): void
    {
        $sut = $this->makeValidator();

        // Token signed with a different secret will fail SignedWith.
        $cfg = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText('a-different-secret-thats-also-32-bytes!'),
        );
        $now = new DateTimeImmutable('2026-05-06T12:00:00+00:00');
        $token = $cfg->builder()
            ->issuedBy('openemr-agentforge')
            ->issuedAt($now)
            ->expiresAt($now->modify('+5 minutes'))
            ->getToken($cfg->signer(), $cfg->signingKey());

        $this->expectException(\Lcobucci\JWT\Validation\RequiredConstraintsViolated::class);
        $sut->validateLookupBearer('Bearer ' . $token->toString());
    }
}
