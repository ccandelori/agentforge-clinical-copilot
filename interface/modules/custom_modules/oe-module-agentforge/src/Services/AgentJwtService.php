<?php

declare(strict_types=1);

/**
 * AgentJwtService — mints short-lived JWTs identifying the OpenEMR user
 * and patient context to the Python sidecar.
 *
 * Tokens are signed with HS256 using a shared symmetric secret; the
 * Python sidecar reads the same secret from AGENTFORGE_JWT_SECRET and
 * verifies on receipt. The token lifetime is intentionally short
 * (5 minutes) — agent turns complete inside this window, and a
 * compromised token expires before it can be widely abused.
 *
 * Claims:
 *   iss   "openemr-agentforge"
 *   sub   user_id (stringified)
 *   iat   issuance time
 *   exp   issuance time + 5 minutes
 *   patient_id          int
 *   username            string (for sensitivity-policy lookup downstream)
 *   role                string (primary GACL group, ARCHITECTURE.md §2)
 *   breakglass_flag     bool
 *   breakglass_reason   string|null
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use InvalidArgumentException;
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use Psr\Clock\ClockInterface;
use RuntimeException;

class AgentJwtService
{
    private const MIN_SECRET_BYTES = 32;
    private const TOKEN_TTL_SECONDS = 300;
    private const ISSUER = 'openemr-agentforge';

    public const ENV_SECRET = 'AGENTFORGE_JWT_SECRET';

    private readonly Configuration $jwtConfig;

    public function __construct(
        string $secret,
        private readonly UserRoleLookup $roleLookup,
        private readonly ClockInterface $clock,
    ) {
        if (strlen($secret) < self::MIN_SECRET_BYTES) {
            throw new InvalidArgumentException(
                'JWT secret must be at least ' . self::MIN_SECRET_BYTES . ' bytes; '
                . 'generate one with `openssl rand -base64 32` or equivalent.'
            );
        }

        $this->jwtConfig = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText($secret),
        );
    }

    /**
     * Build a service from the AGENTFORGE_JWT_SECRET environment variable.
     */
    public static function fromEnvironment(
        UserRoleLookup $roleLookup,
        ClockInterface $clock,
    ): self {
        $secret = getenv(self::ENV_SECRET);
        if ($secret === false || $secret === '') {
            throw new RuntimeException(
                self::ENV_SECRET . ' environment variable is not set; the agent '
                . 'cannot mint authentication tokens. Configure the secret in '
                . 'the deployment\'s environment (sidecar reads the same value).'
            );
        }

        return new self($secret, $roleLookup, $clock);
    }

    /**
     * Mint a signed JWT carrying the agent's user/patient/breakglass context.
     *
     * Role is resolved at mint time via UserRoleLookup. If the user has no
     * GACL group at all (very unusual — typically only happens for partially
     * provisioned accounts), the role claim is null and the sidecar's auth
     * gateway will refuse the token.
     */
    public function mintToken(
        int $userId,
        string $username,
        int $patientId,
        BreakglassContext $breakglass,
    ): string {
        $now = $this->clock->now();
        $role = $this->roleLookup->findPrimaryGroup($username);

        $token = $this->jwtConfig->builder()
            ->issuedBy(self::ISSUER)
            ->relatedTo((string) $userId)
            ->issuedAt($now)
            ->expiresAt($now->modify('+' . self::TOKEN_TTL_SECONDS . ' seconds'))
            ->withClaim('username', $username)
            ->withClaim('patient_id', $patientId)
            ->withClaim('role', $role)
            ->withClaim('breakglass_flag', $breakglass->flag)
            ->withClaim('breakglass_reason', $breakglass->reason)
            ->getToken($this->jwtConfig->signer(), $this->jwtConfig->signingKey());

        return $token->toString();
    }
}
