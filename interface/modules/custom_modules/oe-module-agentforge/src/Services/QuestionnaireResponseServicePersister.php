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
 * collapses it to the five arguments the AgentForge intake-form write
 * path actually needs and validates the result shape.
 *
 * Crucially, the legacy service:
 *   - fires `ServiceSaveEvent::EVENT_PRE_SAVE` and `EVENT_POST_SAVE`
 *     (the rest of OpenEMR listens for these);
 *   - persists the value passed as its 7th positional `$q_id` into
 *     `questionnaire_response.questionnaire_id` and uses it to build
 *     the FHIR canonical URL `{fhirUrl}/Questionnaire/{q_id}` set on
 *     `QuestionnaireResponse.questionnaire`. If `$q_id` is null/empty,
 *     it falls back to the FHIR id parsed from the canonical
 *     Questionnaire JSON (`$q.id`). We pass it explicitly so the
 *     persisted id is never coupled to the (potentially absent or
 *     out-of-sync) `id` field on whatever JSON the lookup returned.
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
 *
 * Note that `$questionnaireName` is NOT passed to the service. The
 * service derives the row's `questionnaire_name` column from the FHIR
 * Questionnaire's `title` (or `name` as fallback) — see lines 402-407
 * of QuestionnaireResponseService::saveQuestionnaireResponse(). We
 * keep the name on the persister interface for narrative-fallback
 * purposes (a future caller might need it for logging / display) and
 * to preserve symmetry with the other identity fields the writer
 * passes through, but at the legacy-service boundary it would shadow
 * the title-from-JSON path and produce an inconsistent record.
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
        string $questionnaireLogicalId,
    ): string {
        unset($questionnaireName); // see class docblock — derived from JSON title.

        $result = $this->service->saveQuestionnaireResponse(
            $questionnaireResponse,           // $response
            $patientId,                       // $pid
            null,                             // $encounter — not bound at intake time
            null,                             // $qr_id — service mints a fresh UUID
            null,                             // $qr_record_id
            $questionnaireJson,               // $q
            $questionnaireLogicalId,          // $q_id — FHIR Questionnaire.id, lands
                                              //         in questionnaire_response.questionnaire_id
                                              //         and Questionnaire/{id} canonical URL
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
