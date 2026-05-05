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
     */
    public static function bridgeAuthorizationHeader(): void
    {
        if (
            !isset($_SERVER['HTTP_AUTHORIZATION'])
            && function_exists('apache_request_headers')
        ) {
            $apacheHeaders = apache_request_headers();
            if (isset($apacheHeaders['Authorization'])) {
                $_SERVER['HTTP_AUTHORIZATION'] = $apacheHeaders['Authorization'];
            }
        }
    }
}
