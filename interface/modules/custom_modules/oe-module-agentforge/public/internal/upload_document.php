<?php

/**
 * Internal endpoint: POST /agentforge/internal/upload_document
 *
 * Called by the Python sidecar's BFF upload route (T38.15) when the
 * dashboard panel receives a multipart upload. The sidecar forwards
 * the user-bound JWT it minted server-side; we validate it with the
 * shared secret. Production deployments should also restrict this URL
 * to the sidecar's IP via reverse-proxy ACL — the JWT check is
 * defense-in-depth rather than the only barrier.
 *
 * Pairs with internal/get_document_bytes.php: that endpoint reads
 * bytes back out of the document store, this one writes them in.
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
use OpenEMR\Modules\AgentForge\Controllers\InternalUploadDocumentController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use OpenEMR\Modules\AgentForge\Services\PatientPidRepository;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

// Internal endpoints don't need a logged-in OpenEMR session — the sidecar
// authenticates via JWT instead. Bypass the session-bound globals path.
$ignoreAuth = true;
require_once dirname(__FILE__, 6) . '/globals.php';
EnvLoader::load();

// Apache + mod_php strips the Authorization header from $_SERVER by
// default. AuthHeaderBridge is the single, audited place that copies
// it back from apache_request_headers() so Symfony's Request can see it.
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

// Build the DBAL connection that PatientPidRepository,
// DocumentUploadWriter, and DocumentIngestAuditWriter need.
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

$writer = new DocumentUploadWriter(
    $connection,
    static fn (): \Document => new \Document(),
);
$auditWriter = new DocumentIngestAuditWriter(
    $connection,
    EventAuditLogger::getInstance(),
);
$patientRepository = new PatientPidRepository($connection);

$controller = new InternalUploadDocumentController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    $patientRepository,
    $writer,
    $auditWriter,
);

$response = $controller->upload(Request::createFromGlobals());
$response->send();
