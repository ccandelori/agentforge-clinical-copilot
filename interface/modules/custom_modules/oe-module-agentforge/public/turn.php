<?php

declare(strict_types=1);

/**
 * Public entry point for `POST /agentforge/turn`.
 *
 * Boots OpenEMR (interface/globals.php), bridges the running PHP
 * session into a Symfony Request, wires up AgentProxyController's
 * dependencies, dispatches, and emits the response.
 *
 * Production deployments can front this with an Apache / Caddy
 * reverse-proxy rewrite to expose `/agentforge/turn` at the root URL
 * rather than under the long module path. The actual sidecar
 * forwarding lives inside the controller (subtask 7.3).
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
use OpenEMR\Modules\AgentForge\Controllers\AgentProxyController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use Symfony\Component\HttpClient\HttpClient;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;

require_once dirname(__FILE__, 5) . '/globals.php';
EnvLoader::load();

// OpenEMR namespaces its session data under $_SESSION['OpenEMR'] (its
// session bag is a sub-array of the native PHP session). Neither
// PhpBridgeSessionStorage nor MockArraySessionStorage reads
// $_SESSION['OpenEMR'][...] for free, so we copy the keys we care about
// across into a Mock-backed Session that the controller can read uniformly
// with the test fixtures.
$openemrSession = is_array($_SESSION['OpenEMR'] ?? null) ? $_SESSION['OpenEMR'] : [];
$session = new Session(new MockArraySessionStorage());
$session->start();
foreach (['pid', 'authUserID', 'authUser', 'breakglass_flag', 'breakglass_reason'] as $key) {
    if (isset($openemrSession[$key])) {
        $session->set($key, $openemrSession[$key]);
    }
}

$request = Request::createFromGlobals();
$request->setSession($session);

// Build a Doctrine DBAL connection from sqlconf for the role lookup.
// (The controller doesn't run hot enough at MVP scale to justify pooling.)
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

$jwtService = AgentJwtService::fromEnvironment(
    new UserRoleLookup($connection),
    ServiceContainer::getClock(),
);

$sidecarBaseUrl = getenv('AGENTFORGE_SIDECAR_URL');
if ($sidecarBaseUrl === false || $sidecarBaseUrl === '') {
    $sidecarBaseUrl = 'http://agentforge-sidecar:8000';
}

$controller = new AgentProxyController(
    jwtService: $jwtService,
    httpClient: HttpClient::create([
        // Idle timeout. The sidecar buffers the synthesis response and
        // only emits when the LLM finishes — so on bulky charts (e.g.
        // pid=8 Eula Crist's 55-problem list) the LLM can take 10-20s
        // to first byte, exceeding a tight 8s idle. Bump to 30s; the
        // SynthesisInputTruncator (Task 45) will eventually cap input
        // volume so synthesis can't run that long, at which point this
        // can come back down.
        'timeout' => 30.0,
        // Hard ceiling so a wedged sidecar still can't hang the user
        // forever. 90s ~= 3x the new idle timeout — matches what
        // Anthropic's longest synthesis-on-bulk-context observably
        // takes today.
        'max_duration' => 90.0,
    ]),
    sidecarBaseUrl: $sidecarBaseUrl,
);
$response = $controller->turn($request);
$response->send();
