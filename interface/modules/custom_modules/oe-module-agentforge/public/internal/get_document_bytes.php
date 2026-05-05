<?php

/**
 * Internal endpoint: GET /agentforge/internal/get_document_bytes?document_id=N
 *
 * Called by the Python sidecar's vision tool when it needs the raw bytes
 * of a lab PDF or scanned intake form. The sidecar forwards the
 * user-bound JWT it received from the browser; we validate it with the
 * same shared secret. Production deployments should also restrict this
 * URL path to the sidecar's IP via reverse-proxy ACL — the JWT check is
 * defense-in-depth rather than the only barrier.
 *
 * The endpoint is bytes-out, not JSON-out: a successful response carries
 * the document body verbatim with the document's recorded MIME type.
 * Any auth/scoping failure path returns JSON instead.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use OpenEMR\Modules\AgentForge\Controllers\InternalDocumentBytesController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentBytesRepository;
use OpenEMR\BC\ServiceContainer;
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
        Response::HTTP_INTERNAL_SERVER_ERROR
    );
    $errorResponse->send();
    return;
}

// DocumentBytesRepository wraps the legacy `Document` class; no DBAL
// connection plumbing needed here (unlike the SQL-backed repositories
// for encounters, labs, etc.).
$controller = new InternalDocumentBytesController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new DocumentBytesRepository(),
);

$response = $controller->show(Request::createFromGlobals());
$response->send();
