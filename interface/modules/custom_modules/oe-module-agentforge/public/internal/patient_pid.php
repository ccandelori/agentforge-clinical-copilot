<?php

/**
 * Internal endpoint: GET /agentforge/internal/patient_pid?patient_uuid=<uuid>
 *
 * Patient-bootstrap endpoint for the dashboard auth bridge
 * (ADR-0001). The Python sidecar calls this to resolve a FHIR
 * Patient resource UUID into the integer ``patient_data.pid`` the
 * legacy AGENTFORGE_JWT contract carries; the sidecar then mints
 * the internal JWT for /turn requests with that pid.
 *
 * Auth: a "lookup-purpose" JWT signed with AGENTFORGE_JWT_SECRET.
 * Same posture as /me. Production deployments should also restrict
 * the URL path to the sidecar's IP via reverse-proxy ACL.
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
use OpenEMR\Modules\AgentForge\Controllers\InternalPatientPidController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

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

$controller = new InternalPatientPidController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new PatientPidRepository($connection),
);

$response = $controller->show(Request::createFromGlobals());
$response->send();
