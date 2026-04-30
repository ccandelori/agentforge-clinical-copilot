<?php

declare(strict_types=1);

/**
 * Public entry point for `POST /agentforge/turn`.
 *
 * Boots OpenEMR (interface/globals.php), bridges the running PHP
 * session into a Symfony Request, dispatches to AgentProxyController,
 * and emits the response.
 *
 * Production deployments can front this with an Apache / Caddy
 * reverse-proxy rewrite to expose `/agentforge/turn` at the root URL
 * rather than under the long module path. The actual sidecar
 * forwarding lives inside the controller (subtasks 7.2 / 7.3).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

use OpenEMR\Modules\AgentForge\Controllers\AgentProxyController;
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

$controller = new AgentProxyController();
$response = $controller->turn($request);
$response->send();
