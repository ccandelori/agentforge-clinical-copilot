<?php

/**
 * QuestionnaireResponseServicePersister — production binding for
 * {@see IntakeQuestionnaireResponsePersister}, delegating to OpenEMR's
 * `QuestionnaireResponseService::saveQuestionnaireResponse()`.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Services;

use OpenEMR\Services\QuestionnaireResponseService;
use RuntimeException;

/**
 * The legacy service has a sprawling positional signature; this class
 * collapses it to the four arguments the AgentForge intake-form write
 * path actually needs and validates the result shape.
 *
 * Crucially, the legacy service:
 *   - fires `ServiceSaveEvent::EVENT_PRE_SAVE` and `EVENT_POST_SAVE`
 *     (the rest of OpenEMR listens for these);
 *   - populates `questionnaire_id` from the FHIR Questionnaire's
 *     logical id (the raw INSERT it replaces did not);
 *   - resolves audit / creator user from the active session (in the
 *     sidecar context the session has no `authUserID`, so both fall
 *     back to null — which is the correct semantics for AI-generated
 *     records pending clinician approval);
 *   - stamps the rendered HTML narrative when `$add_report = true`
 *     (the overlay UI's "extracted by AI, not yet approved" view
 *     reads this back).
 *
 * The legacy service's untyped 10-positional signature is forwarded
 * positionally on purpose — using named arguments would couple us to
 * its parameter names, which are not strictly the public API.
 */
final readonly class QuestionnaireResponseServicePersister implements IntakeQuestionnaireResponsePersister
{
    public function __construct(
        private QuestionnaireResponseService $service,
    ) {
    }

    public function save(
        array $questionnaireResponse,
        int $patientId,
        string $questionnaireJson,
        string $questionnaireName,
    ): string {
        $result = $this->service->saveQuestionnaireResponse(
            $questionnaireResponse,           // $response
            $patientId,                       // $pid
            null,                             // $encounter — not bound at intake time
            null,                             // $qr_id — service mints a fresh UUID
            null,                             // $qr_record_id
            $questionnaireJson,               // $q
            $questionnaireName,               // $q_id (used as title fallback)
            null,                             // $form_response
            true,                             // $add_report — generate narrative
        );

        // The legacy service returns either an array shaped like
        // ['id' => ..., 'response_id' => ..., 'new' => ...] or `false`/
        // an int from the underlying DB call when something goes wrong
        // mid-update. The intake-form path only ever inserts (qr_id is
        // always null going in), so the array shape is the contract,
        // and a missing response_id is a hard failure.
        if (!is_array($result) || !isset($result['response_id']) || !is_string($result['response_id'])) {
            throw new RuntimeException(
                'QuestionnaireResponseService returned an unexpected shape from save',
            );
        }

        return $result['response_id'];
    }
}
