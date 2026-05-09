<?php

/**
 * IntakeQuestionnaireResponseWriter — single-purpose persistence shim
 * for the AgentForge intake-form FHIR QuestionnaireResponse write
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
 * Writes the QuestionnaireResponse row produced by the intake-form
 * persistence endpoint. Deliberately narrow surface — the controller
 * has already done JWT validation, the triple-check, and the FHIR
 * mapping before calling here.
 *
 * History: an earlier revision did a raw INSERT against the
 * `questionnaire_response` table. That bypassed
 * `OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse()`
 * and therefore:
 *   - did not fire `ServiceSaveEvent::EVENT_PRE_SAVE` /
 *     `EVENT_POST_SAVE` (other parts of OpenEMR listen for these);
 *   - did not populate `questionnaire_id` (the FHIR Questionnaire
 *     logical id, separate from the FK into `questionnaire_repository`);
 *   - did not wire creator / audit user fields;
 *   - did not generate the narrative HTML the overlay UI reads.
 *
 * The current implementation routes through the OpenEMR-blessed entry
 * point via the {@see IntakeQuestionnaireResponsePersister} seam (see
 * that interface for why it exists).
 *
 * What this writer still does NOT do:
 *
 * - It does not touch the structured EHR tables (`patient_data`,
 *   `lists`, `medications`, `allergies`, `family_history`). Those
 *   only get populated when a clinician explicitly approves on the
 *   overlay UI; the QuestionnaireResponse is the unapproved record.
 * - It does not fire the AgentForge audit event — that's a separate
 *   concern the controller orchestrates so a write failure doesn't
 *   double-audit and a successful write always pairs with exactly
 *   one event.
 *
 * The returned response_id is the string-form UUID, suitable for
 * surfacing back to the caller as the new resource handle.
 */
readonly class IntakeQuestionnaireResponseWriter
{
    public function __construct(
        private IntakeQuestionnaireResponsePersister $persister,
    ) {
    }

    /**
     * @param array<string, mixed> $questionnaireResponse FHIR R4 JSON
     * @param int $questionnaireForeignId Retained for backward
     *        compatibility with the caller; the persister derives the
     *        FK linkage from the canonical Questionnaire JSON, so this
     *        value is currently unused but kept to preserve the
     *        external API. Removing it would force a same-PR change to
     *        the controller call site for no functional benefit.
     *
     * @return string Newly assigned `response_id` (string-form UUID).
     */
    public function insert(
        int $patientId,
        int $questionnaireForeignId,
        string $questionnaireName,
        array $questionnaireResponse,
        string $questionnaireJson,
    ): string {
        unset($questionnaireForeignId); // see docblock above.

        return $this->persister->save(
            $questionnaireResponse,
            $patientId,
            $questionnaireJson,
            $questionnaireName,
        );
    }
}
