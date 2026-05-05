<?php

declare(strict_types=1);

/**
 * Authorization header bridge for the AgentForge sidecar.
 *
 * Apache + mod_php strips the Authorization header from $_SERVER by default
 * — it's only forwarded when Apache is told to via mod_setenvif, CGIPassAuth,
 * or .htaccess. The header IS available through apache_request_headers(), so
 * this class copies it into $_SERVER['HTTP_AUTHORIZATION'] before Symfony's
 * Request reads from globals.
 *
 * This is the single, audited place where AgentForge writes to a request
 * superglobal. Every internal/*.php entry point delegates here, and PHPStan's
 * ForbiddenRequestGlobalsRule whitelists this class via its ABSTRACTION_CLASSES
 * list. Adding new $_SERVER writes elsewhere is a code smell — the bridging
 * step is conceptually pre-Request infrastructure, and the bridge belongs
 * here, full stop.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

namespace OpenEMR\Modules\AgentForge\Http;

final class AuthHeaderBridge
{
    /**
     * Copy the Authorization header from apache_request_headers() into
     * $_SERVER['HTTP_AUTHORIZATION'] when Apache stripped it. Idempotent —
     * does nothing if the header is already in $_SERVER. Safe to call from
     * non-Apache SAPIs (the function_exists check covers PHP-FPM, CGI, CLI).
     *
     * Header lookup is case-insensitive: HTTP/1.1 (RFC 7230 §3.2) declares
     * field names case-insensitive, and apache_request_headers() does not
     * normalize them — depending on Apache version, mod_php vs PHP-FPM,
     * and proxy chain, the same header surfaces as 'Authorization',
     * 'authorization', or even 'AUTHORIZATION'. A case-sensitive check
     * would 401 a valid request.
     */
    public static function bridgeAuthorizationHeader(): void
    {
        if (isset($_SERVER['HTTP_AUTHORIZATION']) || !function_exists('apache_request_headers')) {
            return;
        }

        $value = self::pickAuthorizationCaseInsensitively(apache_request_headers());
        if ($value !== null) {
            $_SERVER['HTTP_AUTHORIZATION'] = $value;
        }
    }

    /**
     * Case-insensitive lookup of the Authorization header in an array of
     * request headers. Extracted as a pure helper so the case-insensitive
     * matching is unit-testable without mocking the apache_request_headers
     * built-in (which is not available in CLI).
     *
     * @internal Public only for testability — production callers should
     *           use {@see self::bridgeAuthorizationHeader()}.
     *
     * @param array<array-key, mixed> $headers
     */
    public static function pickAuthorizationCaseInsensitively(array $headers): ?string
    {
        foreach ($headers as $name => $value) {
            if (is_string($name) && strcasecmp($name, 'Authorization') === 0 && is_string($value)) {
                return $value;
            }
        }
        return null;
    }
}
