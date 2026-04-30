<?php

declare(strict_types=1);

/**
 * AgentJwtService — mints short-lived JWTs identifying the OpenEMR user
 * and patient context to the Python sidecar.
 *
 * Subtask 6.2 covers the constructor's secret-validation contract and
 * the env-loading factory. Subsequent subtasks layer in role lookup
 * (6.3), break-the-glass propagation (6.4), and the actual mintToken()
 * method (6.5). See ARCHITECTURE.md §2 for the auth gateway design.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Services;

use InvalidArgumentException;
use RuntimeException;

class AgentJwtService
{
    /**
     * HS256 needs at least 256 bits of key material; we require 32 bytes
     * (256 bits) to avoid a foot-gun where a too-short secret silently
     * weakens token security.
     */
    private const MIN_SECRET_BYTES = 32;

    public const ENV_SECRET = 'AGENTFORGE_JWT_SECRET';

    public function __construct(private readonly string $secret)
    {
        if (strlen($this->secret) < self::MIN_SECRET_BYTES) {
            throw new InvalidArgumentException(
                'JWT secret must be at least ' . self::MIN_SECRET_BYTES . ' bytes; '
                . 'generate one with `openssl rand -base64 32` or equivalent.'
            );
        }
    }

    /**
     * Build a service from the AGENTFORGE_JWT_SECRET environment variable.
     *
     * The same env var is read by the Python sidecar so both sides of the
     * trust boundary share the signing material.
     */
    public static function fromEnvironment(): self
    {
        $secret = getenv(self::ENV_SECRET);
        if ($secret === false || $secret === '') {
            throw new RuntimeException(
                self::ENV_SECRET . ' environment variable is not set; the agent '
                . 'cannot mint authentication tokens. Configure the secret in '
                . 'the deployment\'s environment (sidecar reads the same value).'
            );
        }

        return new self($secret);
    }
}
