<?php

declare(strict_types=1);

/**
 * Public entry point for `POST /agentforge/upload_document`.
 *
 * Handles browser-side multipart uploads of lab and intake-form PDFs
 * into the OpenEMR document store. The user must already be logged
 * into OpenEMR (session auth) and have a patient chart open
 * (session pid). The CSRF token is validated against the active
 * OpenEMR session before any controller dispatch.
 *
 * Patient-id authority lives in the session, NOT the multipart
 * payload — see :class:`UploadDocumentController` docblock for the
 * security rationale.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

use Doctrine\DBAL\DriverManager;
use Document;
use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\AgentForge\Controllers\UploadDocumentController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Services\DocumentIngestAuditWriter;
use OpenEMR\Modules\AgentForge\Services\DocumentUploadWriter;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\HttpFoundation\Session\Session;
use Symfony\Component\HttpFoundation\Session\Storage\MockArraySessionStorage;

require_once dirname(__FILE__, 5) . '/globals.php';
EnvLoader::load();

// CSRF check uses the active OpenEMR session (the one session_start()
// already booted via globals.php). Read the token through the
// request bag — direct $_POST access is forbidden by the project's
// PHPStan rule.
$openemrSession = SessionWrapperFactory::getInstance()->getActiveSession();
$bootRequest = Request::createFromGlobals();
$csrfToken = $bootRequest->request->get('csrf_token_form');
if (!is_string($csrfToken) || !CsrfUtils::verifyCsrfToken($csrfToken, $openemrSession)) {
    $errorResponse = new JsonResponse(
        ['error' => 'CSRF validation failed.'],
        Response::HTTP_FORBIDDEN,
    );
    $errorResponse->send();
    return;
}

// Bridge $_SESSION['OpenEMR'][...] into a Mock-backed Symfony Session
// (same pattern as turn.php). The controller reads pid / authUserID /
// authUser / breakglass_* from this bag uniformly across production
// and tests.
$openemrBag = is_array($_SESSION['OpenEMR'] ?? null) ? $_SESSION['OpenEMR'] : [];
$session = new Session(new MockArraySessionStorage());
$session->start();
foreach (['pid', 'authUserID', 'authUser', 'breakglass_flag', 'breakglass_reason'] as $key) {
    if (isset($openemrBag[$key])) {
        $session->set($key, $openemrBag[$key]);
    }
}

$bootRequest->setSession($session);

// Build the DBAL connection that the writer + audit need.
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
    static fn (): Document => new Document(),
);
$auditWriter = new DocumentIngestAuditWriter(
    $connection,
    EventAuditLogger::getInstance(),
);

$controller = new UploadDocumentController($writer, $auditWriter);
$response = $controller->upload($bootRequest);
$response->send();
