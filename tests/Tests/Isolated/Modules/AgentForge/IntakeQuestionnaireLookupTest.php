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

use Doctrine\DBAL\Connection;
use OpenEMR\Modules\AgentForge\Services\IntakeQuestionnaireLookup;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for IntakeQuestionnaireLookup.
 *
 * Two seeded fields matter to the persistence flow:
 *
 *   - `questionnaire_repository.id`           — int FK target
 *   - `questionnaire_repository.questionnaire_id` — FHIR string logical id
 *
 * The lookup must surface BOTH so the writer can persist the logical id
 * into `questionnaire_response.questionnaire_id` (the field FHIR clients
 * resolve `Questionnaire/{id}` against). If the seed row is older and
 * doesn't carry a stored questionnaire_id, fall back to the canonical
 * constant — that keeps existing droplet rows usable without forcing a
 * data migration before deploy.
 */
final class IntakeQuestionnaireLookupTest extends TestCase
{
    #[Test]
    public function returnsSeededRowWithQuestionnaireIdWhenPresent(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAssociative')->willReturn([
            'id' => '7',
            'name' => 'AgentForge Intake Form',
            'questionnaire' => '{"resourceType":"Questionnaire"}',
            'questionnaire_id' => 'agentforge-intake-form',
        ]);

        $lookup = new IntakeQuestionnaireLookup($connection);
        $seeded = $lookup->findCanonicalQuestionnaire();

        self::assertNotNull($seeded);
        self::assertSame(7, $seeded->id);
        self::assertSame('AgentForge Intake Form', $seeded->name);
        self::assertSame('agentforge-intake-form', $seeded->questionnaireId);
    }

    #[Test]
    public function fallsBackToCanonicalConstantWhenStoredQuestionnaireIdIsNull(): void
    {
        // The seed migration in production may have run before this fix
        // and left questionnaire_id NULL. Falling back to the constant
        // keeps the persistence flow working without a coordinated
        // data-fix deploy step.
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAssociative')->willReturn([
            'id' => '7',
            'name' => 'AgentForge Intake Form',
            'questionnaire' => '{}',
            'questionnaire_id' => null,
        ]);

        $lookup = new IntakeQuestionnaireLookup($connection);
        $seeded = $lookup->findCanonicalQuestionnaire();

        self::assertNotNull($seeded);
        self::assertSame(IntakeQuestionnaireLookup::QUESTIONNAIRE_ID, $seeded->questionnaireId);
    }

    #[Test]
    public function returnsNullWhenSeedRowMissing(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('fetchAssociative')->willReturn(false);

        $lookup = new IntakeQuestionnaireLookup($connection);
        self::assertNull($lookup->findCanonicalQuestionnaire());
    }

    #[Test]
    public function questionnaireIdConstantIsValidFhirResourceId(): void
    {
        // FHIR R4 §id: "alphanumeric and dash, max 64 chars". The full
        // [A-Za-z0-9\-\.]{1,64} grammar from the spec; we choose to
        // forbid the dot to keep filename-safe.
        $id = IntakeQuestionnaireLookup::QUESTIONNAIRE_ID;
        self::assertMatchesRegularExpression('/^[A-Za-z0-9-]{1,64}$/', $id);
        self::assertStringNotContainsString(' ', $id);
    }
}
