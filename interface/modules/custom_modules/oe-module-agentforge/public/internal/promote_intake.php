<?php

/**
 * Internal endpoint: POST /agentforge/internal/promote_intake
 *
 * Called by the Python sidecar after a clinician approves rows on the
 * dashboard's intake-extraction review panel. The sidecar forwards the
 * user-bound JWT it received from the browser; we validate it, run the
 * JWT-vs-payload patient-scope check, and write one ``lists`` row per
 * accepted item (allergy / problem / medication / family history).
 *
 * Companion to ``persist_questionnaire_response.php``: that endpoint
 * stores the unapproved AI extraction; this one stores the
 * clinician-approved subset that lands on the chart. The two
 * endpoints share the JWT auth pipeline and the AgentForge audit
 * event family but write to different tables (questionnaire_response
 * vs lists).
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
use OpenEMR\Modules\AgentForge\Controllers\InternalIntakePromoteController;
use OpenEMR\Modules\AgentForge\EnvLoader;
use OpenEMR\Modules\AgentForge\Http\AuthHeaderBridge;
use OpenEMR\Modules\AgentForge\Services\AgentJwtValidator;
use OpenEMR\Modules\AgentForge\Services\IntakePromoteAuditWriter;
use OpenEMR\Modules\AgentForge\Services\IntakePromotionWriter;
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

$controller = new InternalIntakePromoteController(
    new AgentJwtValidator($secret, ServiceContainer::getClock()),
    $connection,
    new IntakePromotionWriter($connection),
    new IntakePromoteAuditWriter($connection, EventAuditLogger::getInstance()),
);

$response = $controller->promote(Request::createFromGlobals());
$response->send();
