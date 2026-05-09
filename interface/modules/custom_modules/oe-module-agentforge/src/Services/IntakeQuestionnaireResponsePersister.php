<?php

/**
 * IntakeQuestionnaireResponsePersister — narrow seam over OpenEMR's
 * QuestionnaireResponseService for the AgentForge intake-form write
 * path.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

/**
 * Why this interface exists:
 *
 * The OpenEMR-blessed entry point for persisting a QuestionnaireResponse
 * is `OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse()`,
 * which fires `ServiceSaveEvent::EVENT_PRE_SAVE` /
 * `EVENT_POST_SAVE` and handles `questionnaire_id` linkage and audit
 * user wiring. We must route through it.
 *
 * BUT `QuestionnaireResponseService` extends `BaseService`, whose file
 * top-level `require_once`s `custom/code_types.inc.php` — which calls
 * `sqlStatement()` immediately. That makes the class un-autoloadable
 * in the isolated-test harness (no DB).
 *
 * This interface is the small adapter seam that lets:
 *
 *   - Production bind to {@see QuestionnaireResponseServicePersister},
 *     which wraps the legacy service.
 *   - Tests mock the interface directly without dragging the legacy
 *     `BaseService` file-include chain into the autoload graph.
 *
 * The signature is intentionally minimized to what the writer needs —
 * the legacy service has 10 positional parameters covering update flows,
 * encounters, and scoring; this seam covers only the create path used
 * by the AgentForge intake-form write.
 */
interface IntakeQuestionnaireResponsePersister
{
    /**
     * Persist a FHIR R4 QuestionnaireResponse for a patient and return
     * the new resource's logical id.
     *
     * @param array<array-key, mixed> $questionnaireResponse FHIR R4 JSON
     * @param int                     $patientId             Internal patient pid
     * @param string                  $questionnaireJson     Serialized canonical
     *                                                       Questionnaire (the
     *                                                       row's source-of-truth
     *                                                       schema column)
     * @param string                  $questionnaireName     Human-readable name;
     *                                                       used as the title
     *                                                       fallback and the
     *                                                       `questionnaire_name`
     *                                                       column.
     *
     * @return string Newly assigned `response_id` (string-form UUID).
     */
    public function save(
        array $questionnaireResponse,
        int $patientId,
        string $questionnaireJson,
        string $questionnaireName,
    ): string;
}
