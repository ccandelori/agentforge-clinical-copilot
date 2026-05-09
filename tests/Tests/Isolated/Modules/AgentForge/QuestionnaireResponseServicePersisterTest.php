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

use OpenEMR\Modules\AgentForge\Services\QuestionnaireResponseServicePersister;
use OpenEMR\Services\QuestionnaireResponseService;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use RuntimeException;

/**
 * Tests the production binding for IntakeQuestionnaireResponsePersister.
 *
 * The load-bearing assertion: the FHIR Questionnaire logical id MUST
 * land in the legacy service's 7th positional argument (`$q_id`),
 * because that argument is what the service writes into
 * `questionnaire_response.questionnaire_id` (line 467 of
 * `OpenEMR\Services\QuestionnaireResponseService::saveQuestionnaireResponse`)
 * and what it uses to construct the FHIR canonical URL
 * `Questionnaire/{id}` (line 443).
 *
 * The previous shape passed the display name ("AgentForge Intake Form")
 * into that slot, which produced the malformed canonical reference
 * `Questionnaire/AgentForge Intake Form` and a junk
 * `questionnaire_response.questionnaire_id` row value.
 *
 * Why an isolated test of the production binding works at all:
 * `QuestionnaireResponseService` extends `BaseService`, whose file
 * `require_once`s `custom/code_types.inc.php`. That include calls
 * `sqlStatement()` at file scope, which fails without a DB. The
 * include guards the call behind `defined('OPENEMR_STATIC_ANALYSIS')`,
 * so this test defines that constant in `setUp()` (matching the prior
 * art in `EncounterRestControllerTest`) before triggering any autoload
 * of the legacy class. PHPUnit's `createMock` then constructs a mock
 * with `disableOriginalConstructor()` (the default), so we never
 * actually run `BaseService::__construct` (which would still hit
 * `QueryUtils::listTableFields()` and fail).
 */
final class QuestionnaireResponseServicePersisterTest extends TestCase
{
    protected function setUp(): void
    {
        // Prevent code_types.inc.php from issuing DB queries when autoloading
        // BaseService (which QuestionnaireResponseService extends).
        if (!defined('OPENEMR_STATIC_ANALYSIS')) {
            define('OPENEMR_STATIC_ANALYSIS', true);
        }
    }

    #[Test]
    public function passesQuestionnaireLogicalIdAsSeventhPositionalArgument(): void
    {
        $service = self::createMock(QuestionnaireResponseService::class);
        $service->expects(self::once())
            ->method('saveQuestionnaireResponse')
            ->with(
                // 1. $response — the FHIR R4 QuestionnaireResponse array
                self::callback(static fn (mixed $arg): bool =>
                    is_array($arg) && ($arg['resourceType'] ?? null) === 'QuestionnaireResponse'),
                // 2. $pid — internal patient id
                self::equalTo(42),
                // 3. $encounter — null at intake time
                self::isNull(),
                // 4. $qr_id — null so the service mints a fresh UUID
                self::isNull(),
                // 5. $qr_record_id — null
                self::isNull(),
                // 6. $q — canonical Questionnaire JSON
                self::equalTo('{"resourceType":"Questionnaire","id":"agentforge-intake-form"}'),
                // 7. $q_id — THE FHIR logical id. This is the load-bearing
                // assertion: NOT the display name, but the kebab-case id.
                self::equalTo('agentforge-intake-form'),
                // 8. $form_response — null
                self::isNull(),
                // 9. $add_report — true so the narrative HTML is generated
                self::isTrue(),
            )
            ->willReturn([
                'id' => 1,
                'response_id' => '11111111-2222-3333-4444-555555555555',
                'new' => true,
            ]);

        $persister = new QuestionnaireResponseServicePersister($service);

        $responseId = $persister->save(
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            patientId: 42,
            questionnaireJson: '{"resourceType":"Questionnaire","id":"agentforge-intake-form"}',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireLogicalId: 'agentforge-intake-form',
        );

        self::assertSame('11111111-2222-3333-4444-555555555555', $responseId);
    }

    #[Test]
    public function doesNotPassDisplayNameAsTheQuestionnaireIdSlot(): void
    {
        // Regression guard for the original P4 bug. If a future
        // refactor "simplifies" the persister by collapsing the two
        // string args, this test must fail. The 7th positional must
        // never be the display name.
        $service = self::createMock(QuestionnaireResponseService::class);
        $service->expects(self::once())
            ->method('saveQuestionnaireResponse')
            ->with(
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::anything(),
                self::logicalNot(self::equalTo('AgentForge Intake Form')),
                self::anything(),
                self::anything(),
            )
            ->willReturn([
                'id' => 1,
                'response_id' => 'rrrrrrrr-rrrr-rrrr-rrrr-rrrrrrrrrrrr',
                'new' => true,
            ]);

        $persister = new QuestionnaireResponseServicePersister($service);
        $persister->save(
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            patientId: 1,
            questionnaireJson: '{}',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireLogicalId: 'agentforge-intake-form',
        );
    }

    #[Test]
    public function returnsResponseIdFromServiceResultArray(): void
    {
        $service = self::createMock(QuestionnaireResponseService::class);
        $service->method('saveQuestionnaireResponse')->willReturn([
            'id' => 7,
            'response_id' => 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
            'new' => true,
        ]);

        $persister = new QuestionnaireResponseServicePersister($service);
        $responseId = $persister->save(
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            patientId: 1,
            questionnaireJson: '{}',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireLogicalId: 'agentforge-intake-form',
        );

        self::assertSame('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', $responseId);
    }

    #[Test]
    public function throwsRuntimeExceptionWhenServiceReturnsUnexpectedShape(): void
    {
        // The legacy service's untyped return contract includes
        // `false`, `int`, and array shapes; the persister narrows to a
        // RuntimeException so the controller's catch block can convert
        // it to a 500 without exposing internals.
        $service = self::createMock(QuestionnaireResponseService::class);
        $service->method('saveQuestionnaireResponse')->willReturn(false);

        $persister = new QuestionnaireResponseServicePersister($service);

        $this->expectException(RuntimeException::class);
        $persister->save(
            questionnaireResponse: ['resourceType' => 'QuestionnaireResponse'],
            patientId: 1,
            questionnaireJson: '{}',
            questionnaireName: 'AgentForge Intake Form',
            questionnaireLogicalId: 'agentforge-intake-form',
        );
    }
}
