<?php

/**
 * AgentJwtValidator — verifies sidecar→PHP internal endpoint requests.
 *
 * The Python sidecar forwards the original user-bound JWT (issued by
 * AgentJwtService) when calling /agentforge/internal/* endpoints. This
 * class validates the signature, issuer, and expiration so the internal
 * endpoint can serve patient data with the same assurance the sidecar's
 * auth gateway already enforces. See ARCHITECTURE.md §4.
 *
 * Two validation modes are exposed:
 *   * validateBearer       — full claim shape (user_id + patient_id);
 *                            used by the legacy /internal/* endpoints.
 *   * validateLookupBearer — signature + issuer + expiration only,
 *                            no claim shape; used by the dashboard
 *                            auth bridge (ADR-0001) when bootstrapping
 *                            identity it doesn't yet know.
 *
 * The clock is injected as a PSR-20 ClockInterface (rather than
 * Lcobucci\Clock\Clock) so the validator stays in step with the rest of
 * the OpenEMR clock-injection conventions; expiration is checked
 * manually rather than via lcobucci's StrictValidAt constraint.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use InvalidArgumentException;
use Lcobucci\JWT\Configuration;
use Lcobucci\JWT\Signer\Hmac\Sha256;
use Lcobucci\JWT\Signer\Key\InMemory;
use Lcobucci\JWT\Token;
use Lcobucci\JWT\UnencryptedToken;
use Lcobucci\JWT\Validation\Constraint\IssuedBy;
use Lcobucci\JWT\Validation\Constraint\SignedWith;
use Psr\Clock\ClockInterface;
use RuntimeException;

class AgentJwtValidator
{
    private const ISSUER = 'openemr-agentforge';

    private readonly Configuration $jwtConfig;

    public function __construct(string $secret, private readonly ClockInterface $clock)
    {
        if ($secret === '') {
            throw new InvalidArgumentException('JWT secret must be a non-empty string');
        }

        $this->jwtConfig = Configuration::forSymmetricSigner(
            new Sha256(),
            InMemory::plainText($secret),
        );
        $this->jwtConfig->setValidationConstraints(
            new SignedWith($this->jwtConfig->signer(), $this->jwtConfig->verificationKey()),
            new IssuedBy(self::ISSUER),
        );
    }

    /**
     * Parse + validate the bearer token. Throws on invalid/expired/wrong-issuer
     * tokens; returns a typed claims DTO on success. Callers should catch the
     * lcobucci validation exceptions (or RuntimeException) and respond 401.
     */
    public function validateBearer(string $authorizationHeader): ValidatedClaims
    {
        $prefix = 'Bearer ';
        if (!str_starts_with($authorizationHeader, $prefix)) {
            throw new RuntimeException('Authorization scheme must be Bearer');
        }
        $tokenString = substr($authorizationHeader, strlen($prefix));

        $token = $this->jwtConfig->parser()->parse($tokenString);
        if (!$token instanceof UnencryptedToken) {
            throw new RuntimeException('Token is not an unencrypted JWT');
        }

        $constraints = $this->jwtConfig->validationConstraints();
        $this->jwtConfig->validator()->assert($token, ...$constraints);

        // Manual expiration check — PSR clock keeps us aligned with the rest
        // of OpenEMR's clock-injection conventions; lcobucci's StrictValidAt
        // requires Lcobucci\Clock\Clock which doesn't fit.
        if ($token->isExpired($this->clock->now())) {
            throw new RuntimeException('Token has expired');
        }

        $claims = $token->claims();
        $patientId = $claims->get('patient_id');
        if (!is_int($patientId) || $patientId <= 0) {
            throw new RuntimeException('patient_id claim missing or invalid');
        }

        $sub = $claims->get(Token\RegisteredClaims::SUBJECT);
        $userId = is_string($sub) ? (int) $sub : 0;
        if ($userId <= 0) {
            throw new RuntimeException('sub claim missing or invalid');
        }

        return new ValidatedClaims(userId: $userId, patientId: $patientId);
    }

    /**
     * Lookup-purpose variant: validates signature, issuer, and
     * expiration but does NOT require user/patient claims.
     *
     * The dashboard auth bridge (ADR-0001) calls /me before it can
     * mint a "real" internal JWT — by construction the lookup request
     * doesn't yet know the integer user_id or patient_id. The bridge
     * mints a minimal JWT (correctly signed, correct issuer, short
     * exp) and this method confirms it without reading claim shape.
     *
     * Callers should catch the lcobucci validation exceptions (or
     * RuntimeException) and respond 401.
     */
    public function validateLookupBearer(string $authorizationHeader): void
    {
        $prefix = 'Bearer ';
        if (!str_starts_with($authorizationHeader, $prefix)) {
            throw new RuntimeException('Authorization scheme must be Bearer');
        }
        $tokenString = substr($authorizationHeader, strlen($prefix));

        $token = $this->jwtConfig->parser()->parse($tokenString);
        if (!$token instanceof UnencryptedToken) {
            throw new RuntimeException('Token is not an unencrypted JWT');
        }

        $constraints = $this->jwtConfig->validationConstraints();
        $this->jwtConfig->validator()->assert($token, ...$constraints);

        if ($token->isExpired($this->clock->now())) {
            throw new RuntimeException('Token has expired');
        }
    }
}
