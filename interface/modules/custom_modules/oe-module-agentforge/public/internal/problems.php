<?php

declare(strict_types=1);

/**
 * Internal endpoint: GET /agentforge/internal/problems?pid=N
 *
 * Called by the Python sidecar's get_active_problems tool. The sidecar
 * forwards the user-bound JWT it received from the browser; we validate
 * it with the same shared secret. Production deployments should also
 * restrict this URL path to the sidecar's IP via reverse-proxy ACL —
 * the JWT check is defense-in-depth rather than the only barrier.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

use Doctrine\DBAL\DriverManager;
use OpenEMR\BC\ServiceContainer;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\AgentForge\Controllers\InternalProblemsController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\ProblemsRepository;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

// Internal endpoints don't need a logged-in OpenEMR session — the sidecar
// authenticates via JWT instead. Bypass the session-bound globals path.
$ignoreAuth = true;
require_once dirname(__FILE__, 6) . '/globals.php';
EnvLoader::load();

// Apache + mod_php strips the Authorization header from $_SERVER by default
// (it's only forwarded when Apache is told to via mod_setenvif / CGIPassAuth /
// htaccess). The header IS available via apache_request_headers(), so we
// copy it back into $_SERVER before Symfony's Request reads from globals.
if (
    !isset($_SERVER['HTTP_AUTHORIZATION'])
    && function_exists('apache_request_headers')
) {
    $apacheHeaders = apache_request_headers();
    if (isset($apacheHeaders['Authorization'])) {
        $_SERVER['HTTP_AUTHORIZATION'] = $apacheHeaders['Authorization'];
    }
}

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

$controller = new InternalProblemsController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new ProblemsRepository($connection),
);

$response = $controller->show(Request::createFromGlobals());
$response->send();
