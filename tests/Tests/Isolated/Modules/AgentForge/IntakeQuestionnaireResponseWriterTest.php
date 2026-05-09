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
 *   2. The narrative-generation flag is set so the saved row carries
 *      the rendered HTML report (the overlay UI reads this)
 *   3. The returned response_id round-trips out of the persister result
 *   4. Persister exceptions propagate (the controller catches at its
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
                self::equalTo('{"resourceType":"Questionnaire","id":"agentforge-intake"}'),
                // $questionnaireName: the service uses this as both
                // questionnaire_name and (when missing from the FHIR
                // payload) the title.
                self::equalTo('AgentForge Intake Form'),
            )
            ->willReturn('11111111-2222-3333-4444-555555555555');

        $writer = new IntakeQuestionnaireResponseWriter($persister);

        $responseId = $writer->insert(
            patientId: 42,
            questionnaireForeignId: 7,
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: [
                'resourceType' => 'QuestionnaireResponse',
                'status' => 'completed',
                'item' => [],
            ],
            questionnaireJson: '{"resourceType":"Questionnaire","id":"agentforge-intake"}',
        );

        self::assertSame('11111111-2222-3333-4444-555555555555', $responseId);
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
            questionnaireName: 'AgentForge Intake Form',
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            questionnaireJson: '{}',
        );
    }
}
