<?php

/**
 * Internal endpoint: POST /agentforge/internal/persist_lab_result
 *
 * Called by the Python sidecar after the lab worker has extracted a
 * structured LabPdfExtraction from a scanned lab PDF. We validate the
 * forwarded user-bound JWT, run the JWT-vs-payload-vs-document
 * triple-check, and write the cascade
 * (procedure_order → procedure_report → N×procedure_result) atomically.
 *
 * The procedure_result rows are the unapproved record. The
 * `review_status` column is set to 'received' so they land in the
 * clinician's lab-list inbox for review; structured downstream tables
 * are NOT touched here.
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
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\AgentForge\Controllers\InternalLabPersistController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentOwnershipVerifier;
use OpenEMR\Modules\AgentForge\Services\LabPersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\LabResultWriter;
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
        Response::HTTP_INTERNAL_SERVER_ERROR,
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

$controller = new InternalLabPersistController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new DocumentOwnershipVerifier($connection),
    new LabResultWriter($connection),
    new LabPersistAuditWriter($connection, EventAuditLogger::getInstance()),
);

$response = $controller->persist(Request::createFromGlobals());
$response->send();
