<?php

/**
 * Internal endpoint: GET /agentforge/internal/me?user_uuid=<uuid>
 *
 * Identity-bootstrap endpoint for the dashboard auth bridge (ADR-0001).
 * The Python sidecar calls this once per dashboard session to resolve
 * the OIDC user UUID into the integer user_id + username + primary
 * GACL group needed by the legacy AGENTFORGE_JWT contract; the sidecar
 * then mints "real" internal JWTs for /turn requests with those claims.
 *
 * Auth: a "lookup-purpose" JWT signed with AGENTFORGE_JWT_SECRET. The
 * validator confirms signature, issuer, and expiration only — claim
 * shape is not yet known at the moment of the lookup. Production
 * deployments should also restrict this URL path to the sidecar's IP
 * via reverse-proxy ACL; the JWT check is defense-in-depth.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use Doctrine\DBAL\DriverManager;
use OpenEMR\BC\ServiceContainer;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\AgentForge\Controllers\InternalMeController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\UserIdentityRepository;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

// Internal endpoints don't need a logged-in OpenEMR session — the sidecar
// authenticates via JWT instead. Bypass the session-bound globals path.
$ignoreAuth = true;
require_once dirname(__FILE__, 6) . '/globals.php';
EnvLoader::load();

AuthHeaderBridge::bridgeAuthorizationHeader();

$secret = getenv('AGENTFORGE_JWT_SECRET');
if ($secret === false || $secret === '') {
    $errorResponse = new JsonResponse(
        ['error' => 'AGENTFORGE_JWT_SECRET not configured'],
        Response::HTTP_INTERNAL_SERVER_ERROR
    );
    $errorResponse->send();
    return;
}

$sqlconfRaw = OEGlobalsBag::getInstance()->get('sqlconf');
/** @var array{dbase: string, login: string, pass: string, host: string, port?: int|string} $sqlconf */
$sqlconf = is_array($sqlconfRaw) ? $sqlconfRaw : [];
$port = $sqlconf['port'] ?? 3306;
$connection = DriverManager::getConnection([
    'dbname' => $sqlconf['dbase'],
    'user' => $sqlconf['login'],
    'password' => $sqlconf['pass'],
    'host' => $sqlconf['host'],
    'port' => is_int($port) ? $port : (int) $port,
    'driver' => 'pdo_mysql',
]);

$controller = new InternalMeController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new UserIdentityRepository($connection),
    new UserRoleLookup($connection),
);

$response = $controller->show(Request::createFromGlobals());
$response->send();
