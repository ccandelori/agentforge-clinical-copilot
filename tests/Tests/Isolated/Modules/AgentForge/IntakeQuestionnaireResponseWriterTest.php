<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\AgentForge;

use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireResponsePersister;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireResponseWriter;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for IntakeQuestionnaireResponseWriter — the AgentForge
 * intake-form persistence shim.
 *
 * The original implementation did a raw `INSERT INTO questionnaire_response`,
 * which skipped:
 *   - `ServiceSaveEvent::EVENT_PRE_SAVE` / `EVENT_POST_SAVE` event firing
 *     (other parts of OpenEMR listen for these to keep auxiliary state
 *      consistent — bypassing them quietly desyncs)
 *   - `questionnaire_id` linkage (the canonical FHIR Questionnaire id,
 *     not just the foreign-key into questionnaire_repository)
 *   - Creator / audit user wiring (filled from session by the service)
 *   - Generated narrative behavior
 *
 * The fix delegates through `IntakeQuestionnaireResponsePersister` —
 * an interface owned by this module whose production binding wraps
 * `OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse()`,
 * the OpenEMR-blessed entry point that fires the service events.
 *
 * Why the interface seam: `QuestionnaireResponseService` extends
 * `BaseService`, which `require_once`s `custom/code_types.inc.php` at
 * file-include time. That file calls `sqlStatement()`, so even
 * autoloading the class fails in the isolated test harness (no DB).
 * The thin interface lets the writer remain unit-testable while
 * production wires through the legacy class.
 *
 * These tests verify:
 *   1. `insert()` calls the persister exactly once with the right shape
 *   2. The FHIR Questionnaire logical id passes through verbatim — it
 *      lands in the persister's `$questionnaireLogicalId` slot, the
 *      production binding then forwards it as the legacy service's 7th
 *      positional `$q_id` so it persists into
 *      `questionnaire_response.questionnaire_id` (the column FHIR
 *      clients use to resolve `Questionnaire/{id}`).
 *   3. The display name and the logical id are kept distinct — the
 *      previous version of this writer's persister passed the display
 *      name as the legacy service's `$q_id`, which silently produced
 *      `Questionnaire/AgentForge Intake Form` (broken FHIR canonical).
 *   4. The returned response_id round-trips out of the persister result
 *   5. Persister exceptions propagate (the controller catches at its
 *      layer; the writer must not swallow)
 */
final class IntakeQuestionnaireResponseWriterTest extends TestCase
{
    #[Test]
    public function insertDelegatesToPersisterWithExpectedArguments(): void
    {
        $persister = self::createMock(IntakeQuestionnaireResponsePersister::class);
        $persister->expects(self::once())
            ->method('save')
            ->with(
                // $response: the FHIR QuestionnaireResponse array, untouched.
                self::callback(static fn (mixed $arg): bool => is_array($arg)
                    && ($arg['resourceType'] ?? null) === 'QuestionnaireResponse'
                    && ($arg['status'] ?? null) === 'completed'),
                // $patientId
                self::equalTo(42),
                // $questionnaireJson: the canonical Questionnaire JSON,
                // passed through.
                self::equalTo('{"resourceType":"Questionnaire","id":"agentforge-intake-form"}'),
                // $questionnaireName: human display name. Kept on the
                // interface for narrative-fallback / logging purposes
                // even though the production binding does not forward
                // it to the legacy service (the service derives the
                // row's name column from the canonical JSON's title).
                self::equalTo('AgentForge Intake Form'),
                // $questionnaireLogicalId: FHIR R4 Questionnaire.id —
                // distinct from the display name, used to construct
                // `Questionnaire/{id}` canonical references.
                self::equalTo('agentforge-intake-form'),
            )
            ->willReturn('11111111-2222-3333-4444-555555555555');

        $writer = new IntakeQuestionnaireResponseWriter($persister);

        $responseId = $writer->insert(
            patientId: 42,
            questionnaireForeignId: 7,
            questionnaireId: 'agentforge-intake-form',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: [
                'resourceType' => 'QuestionnaireResponse',
                'status' => 'completed',
                'item' => [],
            ],
            questionnaireJson: '{"resourceType":"Questionnaire","id":"agentforge-intake-form"}',
        );

        self::assertSame('11111111-2222-3333-4444-555555555555', $responseId);
    }

    #[Test]
    public function insertForwardsLogicalIdVerbatimToPersister(): void
    {
        // The load-bearing contract: whatever logical id the controller
        // supplies (sourced from the seeded canonical Questionnaire row)
        // must reach the persister untouched. No rewriting, no
        // case-folding, no falling back to the display name.
        //
        // This guards against a future refactor that "helpfully"
        // derives the id from the name — the previous bug shape.
        $captured = null;
        $persister = self::createMock(IntakeQuestionnaireResponsePersister::class);
        $persister->method('save')
            ->willReturnCallback(static function (
                array $response,
                int $patientId,
                string $questionnaireJson,
                string $questionnaireName,
                string $questionnaireLogicalId,
            ) use (&$captured): string {
                $captured = $questionnaireLogicalId;
                return 'cccccccc-cccc-cccc-cccc-cccccccccccc';
            });

        $writer = new IntakeQuestionnaireResponseWriter($persister);

        $writer->insert(
            patientId: 1,
            questionnaireForeignId: 7,
            questionnaireId: 'agentforge-intake-form',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            questionnaireJson: '{}',
        );

        self::assertSame('agentforge-intake-form', $captured);
    }

    #[Test]
    public function insertKeepsLogicalIdAndDisplayNameDistinct(): void
    {
        // Regression guard for the original P4 bug shape: the legacy
        // QuestionnaireResponseService's 7th positional was being
        // passed the display name ("AgentForge Intake Form"), which is
        // not a valid FHIR resource id. The writer must hand the
        // persister two separate values and not collapse them.
        $persister = self::createMock(IntakeQuestionnaireResponsePersister::class);
        $persister->expects(self::once())
            ->method('save')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::equalTo('AgentForge Intake Form'),     // display name
                self::equalTo('agentforge-intake-form'),     // logical id
            )
            ->willReturn('dddddddd-dddd-dddd-dddd-dddddddddddd');

        $writer = new IntakeQuestionnaireResponseWriter($persister);
        $writer->insert(
            patientId: 1,
            questionnaireForeignId: 7,
            questionnaireId: 'agentforge-intake-form',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            questionnaireJson: '{}',
        );
    }

    #[Test]
    public function insertReturnsTheResponseIdProducedByThePersister(): void
    {
        $persister = self::createMock(IntakeQuestionnaireResponsePersister::class);
        $persister->method('save')->willReturn('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee');

        $writer = new IntakeQuestionnaireResponseWriter($persister);

        $responseId = $writer->insert(
            patientId: 1,
            questionnaireForeignId: 7,
            questionnaireId: 'agentforge-intake-form',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            questionnaireJson: '{}',
        );

        self::assertSame('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', $responseId);
    }

    #[Test]
    public function insertPropagatesPersisterExceptionsRatherThanSwallowing(): void
    {
        // The controller catches DbalException | JsonException |
        // RuntimeException at its layer. The writer must not swallow —
        // that would hide failures and the controller would 200 a
        // phantom write.
        $persister = self::createMock(IntakeQuestionnaireResponsePersister::class);
        $persister->method('save')
            ->willThrowException(new \RuntimeException('db unavailable'));

        $writer = new IntakeQuestionnaireResponseWriter($persister);

        $this->expectException(\RuntimeException::class);
        $writer->insert(
            patientId: 1,
            questionnaireForeignId: 7,
            questionnaireId: 'agentforge-intake-form',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            questionnaireJson: '{}',
        );
    }
}
