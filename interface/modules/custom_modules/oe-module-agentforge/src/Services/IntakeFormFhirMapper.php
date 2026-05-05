<?php

/**
 * IntakeFormFhirMapper — pure transformation from the sidecar's
 * IntakeFormExtraction shape (the Task 4 Pydantic model serialized to
 * JSON) into a FHIR R4 QuestionnaireResponse `item` tree.
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
 * Maps the IntakeFormExtraction JSON shape onto the canonical FHIR
 * Questionnaire structure seeded by the W2 Task 5 migration. linkIds
 * mirror the Pydantic field names one-to-one so this mapper is
 * largely a structural recasting — no translation table.
 *
 * What this mapper does NOT do:
 *
 * - It does NOT validate. The caller (the persistence controller) is
 *   responsible for parsing the JSON body and confirming required
 *   fields are present before calling here. This stays a pure
 *   transformation so it's testable in isolation.
 * - It does NOT carry citations into the FHIR payload. Citations are
 *   the bridge to the overlay UI but the structured FHIR side is the
 *   "approved" record; preserving citations there would conflate "the
 *   AI extracted this" with "the clinician approved this". The
 *   citations stay on the sidecar side.
 * - It does NOT write to the EHR's structured tables (patient_data,
 *   medications, allergies, family_history). The QuestionnaireResponse
 *   IS the data store at this stage; the structured tables only get
 *   populated when a clinician clicks "approve" on the overlay UI.
 *
 * The returned array is a FHIR R4 QuestionnaireResponse (without the
 * `id`, which the database assigns) ready to be JSON-encoded for the
 * `questionnaire_response.questionnaire_response` longtext column.
 */
final class IntakeFormFhirMapper
{
    /**
     * @param array<array-key, mixed> $extraction IntakeFormExtraction JSON
     * @return array<string, mixed> FHIR QuestionnaireResponse
     */
    public static function toQuestionnaireResponse(
        array $extraction,
        string $questionnaireUrl,
    ): array {
        $items = [
            self::chiefConcernItem($extraction),
            self::demographicsItem($extraction),
            ...self::medicationsItems($extraction),
            ...self::allergiesItems($extraction),
            ...self::familyHistoryItems($extraction),
        ];

        // Drop empty items (chief_concern with no answer, demographics
        // with no entries) — FHIR permits, but downstream renderers do
        // cleaner things with absence than with empty `answer` arrays.
        $items = array_values(array_filter(
            $items,
            static function (array $item): bool {
                $answer = $item['answer'] ?? [];
                $sub = $item['item'] ?? [];
                return (is_array($answer) && count($answer) > 0)
                    || (is_array($sub) && count($sub) > 0);
            },
        ));

        return [
            'resourceType' => 'QuestionnaireResponse',
            'questionnaire' => $questionnaireUrl,
            'status' => 'completed',
            'item' => $items,
        ];
    }

    /**
     * @param array<array-key, mixed> $extraction
     * @return array<string, mixed>
     */
    private static function chiefConcernItem(array $extraction): array
    {
        $value = $extraction['chief_concern'] ?? null;
        if (!is_string($value) || $value === '') {
            return ['linkId' => 'chief_concern', 'answer' => []];
        }
        return [
            'linkId' => 'chief_concern',
            'answer' => [['valueString' => $value]],
        ];
    }

    /**
     * @param array<array-key, mixed> $extraction
     * @return array<string, mixed>
     */
    private static function demographicsItem(array $extraction): array
    {
        $rows = $extraction['demographics'] ?? [];
        if (!is_array($rows)) {
            return ['linkId' => 'demographics', 'item' => []];
        }
        $items = [];
        foreach ($rows as $row) {
            if (!is_array($row)) {
                continue;
            }
            $field = $row['field'] ?? null;
            $value = $row['value'] ?? null;
            if (!is_string($field) || $field === '' || !is_string($value)) {
                continue;
            }
            $items[] = [
                'linkId' => $field,
                'answer' => [['valueString' => $value]],
            ];
        }
        return [
            'linkId' => 'demographics',
            'item' => $items,
        ];
    }

    /**
     * @param array<array-key, mixed> $extraction
     * @return list<array<string, mixed>>
     */
    private static function medicationsItems(array $extraction): array
    {
        $rows = $extraction['medications'] ?? [];
        if (!is_array($rows)) {
            return [];
        }
        $items = [];
        foreach ($rows as $row) {
            if (!is_array($row)) {
                continue;
            }
            $name = $row['name'] ?? null;
            if (!is_string($name) || $name === '') {
                continue;
            }
            $sub = [['linkId' => 'name', 'answer' => [['valueString' => $name]]]];
            $dose = $row['dose'] ?? null;
            if (is_string($dose) && $dose !== '') {
                $sub[] = ['linkId' => 'dose', 'answer' => [['valueString' => $dose]]];
            }
            $frequency = $row['frequency'] ?? null;
            if (is_string($frequency) && $frequency !== '') {
                $sub[] = ['linkId' => 'frequency', 'answer' => [['valueString' => $frequency]]];
            }
            $items[] = ['linkId' => 'medications', 'item' => $sub];
        }
        return $items;
    }

    /**
     * @param array<array-key, mixed> $extraction
     * @return list<array<string, mixed>>
     */
    private static function allergiesItems(array $extraction): array
    {
        $rows = $extraction['allergies'] ?? [];
        if (!is_array($rows)) {
            return [];
        }
        $items = [];
        foreach ($rows as $row) {
            if (!is_array($row)) {
                continue;
            }
            $substance = $row['substance'] ?? null;
            if (!is_string($substance) || $substance === '') {
                continue;
            }
            $sub = [['linkId' => 'substance', 'answer' => [['valueString' => $substance]]]];
            $reaction = $row['reaction'] ?? null;
            if (is_string($reaction) && $reaction !== '') {
                $sub[] = ['linkId' => 'reaction', 'answer' => [['valueString' => $reaction]]];
            }
            $severity = $row['severity'] ?? null;
            if (is_string($severity) && $severity !== '') {
                $sub[] = ['linkId' => 'severity', 'answer' => [['valueString' => $severity]]];
            }
            $items[] = ['linkId' => 'allergies', 'item' => $sub];
        }
        return $items;
    }

    /**
     * @param array<array-key, mixed> $extraction
     * @return list<array<string, mixed>>
     */
    private static function familyHistoryItems(array $extraction): array
    {
        $rows = $extraction['family_history'] ?? [];
        if (!is_array($rows)) {
            return [];
        }
        $items = [];
        foreach ($rows as $row) {
            if (!is_array($row)) {
                continue;
            }
            $relative = $row['relative'] ?? null;
            $condition = $row['condition'] ?? null;
            if (!is_string($relative) || $relative === '' || !is_string($condition) || $condition === '') {
                continue;
            }
            $items[] = [
                'linkId' => 'family_history',
                'item' => [
                    ['linkId' => 'relative', 'answer' => [['valueString' => $relative]]],
                    ['linkId' => 'condition', 'answer' => [['valueString' => $condition]]],
                ],
            ];
        }
        return $items;
    }
}
