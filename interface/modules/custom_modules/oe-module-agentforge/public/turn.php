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
use OpenEMR\Modules\AgentForge\Services\AgentJwtService;
use OpenEMR\Modules\AgentForge\Services\UserRoleLookup;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\PhpBridgeSessionStorage;

require_once dirname(__FILE__, 5) . '/globals.php';

// Bridge OpenEMR's native PHP session into Symfony's Session abstraction
// so the controller can use Request::getSession() consistently with tests.
$session = new Session(new PhpBridgeSessionStorage());
$session->start();

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

$controller = new AgentProxyController($jwtService);
$response = $controller->turn($request);
$response->send();
