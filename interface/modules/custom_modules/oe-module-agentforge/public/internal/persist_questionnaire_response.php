<?php

/**
 * Internal endpoint: POST /agentforge/internal/persist_questionnaire_response
 *
 * Called by the Python sidecar after the intake worker has extracted a
 * structured IntakeFormExtraction from a scanned intake-form PDF. The
 * sidecar forwards the user-bound JWT it received from the browser; we
 * validate it, run the JWT-vs-payload-vs-document triple-check, map
 * the extraction onto a FHIR QuestionnaireResponse, and persist it
 * against the canonical AgentForge intake-form Questionnaire seeded
 * by the W2 Task 5 migration.
 *
 * The QuestionnaireResponse is the unapproved record. Structured EHR
 * tables (patient_data, medications, allergies, family_history) are
 * NOT touched here — those only get written when a clinician
 * explicitly approves on the overlay UI (a later W2 task). The
 * QuestionnaireResponse is what the overlay reads back to render the
 * "extracted by AI, not yet approved" view.
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
use OpenEMR\Modules\AgentForge\Controllers\InternalIntakePersistController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\DocumentOwnershipVerifier;
use OpenEMR\Modules\AgentForge\Services\IntakePersistAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireLookup;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireResponseWriter;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

// Internal endpoints don't need a logged-in OpenEMR session — the
// sidecar authenticates via JWT instead.
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

$controller = new InternalIntakePersistController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    new DocumentOwnershipVerifier($connection),
    new IntakeQuestionnaireLookup($connection),
    new IntakeQuestionnaireResponseWriter($connection),
    new IntakePersistAuditWriter($connection, EventAuditLogger::getInstance()),
);

$response = $controller->persist(Request::createFromGlobals());
$response->send();
