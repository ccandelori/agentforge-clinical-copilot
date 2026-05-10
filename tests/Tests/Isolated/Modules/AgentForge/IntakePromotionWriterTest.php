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
use OpenEMR\Modules\AgentForge\Services\IntakePromotionWriter;
use OpenEMR\Modules\AgentForge\Services\PromotionItem;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Behavior tests for {@see IntakePromotionWriter}.
 *
 * The writer is a thin INSERT cascade wrapped in a DBAL
 * transactional() call; these tests pin (a) the SQL shape and bind
 * values, (b) the comments-string formatting that carries the
 * lineage hint into the chart row, and (c) the rollback contract
 * (a per-row insert failure rolls back the whole batch).
 */
final class IntakePromotionWriterTest extends TestCase
{
    #[Test]
    public function persistsOneListsRowPerItemWithCorrectShape(): void
    {
        $captured = [];
        $idCounter = 100;

        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$captured, &$idCounter): int {
                $captured[] = ['sql' => $sql, 'bind' => $bind];
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturnCallback(
            static fn () => (string) ++$idCounter,
        );

        $writer = new IntakePromotionWriter($connection);

        $result = $writer->persist(
            patientId: 42,
            username: 'admin',
            questionnaireResponseId: 'qr-1',
            documentId: 777,
            items: [
                new PromotionItem(kind: 'allergy', title: 'Penicillin', details: 'rash'),
                new PromotionItem(kind: 'medical_problem', title: 'Type 2 diabetes'),
            ],
        );

        self::assertCount(2, $captured);
        // First row — allergy.
        self::assertStringContainsString('INSERT INTO lists', $captured[0]['sql']);
        self::assertSame(42, $captured[0]['bind']['pid']);
        self::assertSame('allergy', $captured[0]['bind']['type']);
        self::assertSame('Penicillin', $captured[0]['bind']['title']);
        // Allergy rows now populate ``diagnosis`` so the FHIR
        // projection can build a real ``code.text`` instead of
        // dropping into the data-absent-unknown branch.
        self::assertSame('Penicillin', $captured[0]['bind']['diagnosis']);
        self::assertStringContainsString('rash', $captured[0]['bind']['comments']);
        self::assertStringContainsString('qr_id=qr-1', $captured[0]['bind']['comments']);
        self::assertStringContainsString('doc_id=777', $captured[0]['bind']['comments']);
        self::assertSame('admin', $captured[0]['bind']['user']);
        // Second row — problem (still uses the minimal-shape insert,
        // no ``diagnosis`` bind because the generic INSERT doesn't
        // reference that column).
        self::assertSame('medical_problem', $captured[1]['bind']['type']);
        self::assertSame('Type 2 diabetes', $captured[1]['bind']['title']);
        self::assertArrayNotHasKey('diagnosis', $captured[1]['bind']);

        // Result handles match insertion order with the synthetic ids
        // produced by the lastInsertId stub.
        self::assertCount(2, $result->handles);
        self::assertSame('allergy', $result->handles[0]->kind);
        self::assertSame(101, $result->handles[0]->listsId);
        self::assertSame('medical_problem', $result->handles[1]->kind);
        self::assertSame(102, $result->handles[1]->listsId);
    }

    #[Test]
    public function allergyInsertSetsSeverityWhenDetailsCarryRoundTripBucket(): void
    {
        // ExtractionPanel formats allergy details as
        // ``"<reaction> (<severity>)"`` — the writer parses the
        // trailing parenthesised group and only writes
        // ``severity_al`` when the bucket round-trips cleanly through
        // FHIR criticality back to the frontend's three-value enum.
        $captured = null;
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$captured): int {
                $captured = ['sql' => $sql, 'bind' => $bind];
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 7,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [new PromotionItem(
                kind: 'allergy',
                title: 'Penicillin',
                details: 'Hives all over body (severe)',
            )],
        );

        self::assertNotNull($captured);
        self::assertSame('Penicillin', $captured['bind']['diagnosis']);
        self::assertSame('severe', $captured['bind']['severity_al']);
        // The SQL needs to actually include the column, not just
        // carry an unbound parameter.
        self::assertStringContainsString('severity_al', $captured['sql']);
    }

    #[Test]
    public function allergyInsertOmitsSeverityForNonRoundTripBuckets(): void
    {
        // ``moderate`` does NOT round-trip cleanly: severity_al
        // 'moderate' → criticality 'low' → frontend severity 'mild'.
        // The writer's contract is to leave the column NULL in that
        // case so the frontend's downstream default of 'moderate'
        // surfaces — which is the honest answer.
        $rows = [];
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$rows): int {
                $rows[] = ['sql' => $sql, 'bind' => $bind];
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Sulfa drugs',
                    details: 'rash (moderate)',
                ),
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Latex',
                    details: null,
                ),
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Peanut',
                    details: 'anaphylaxis',
                ),
            ],
        );

        self::assertCount(3, $rows);
        // moderate → omitted
        self::assertArrayNotHasKey('severity_al', $rows[0]['bind']);
        self::assertStringNotContainsString('severity_al', $rows[0]['sql']);
        // null details → omitted
        self::assertArrayNotHasKey('severity_al', $rows[1]['bind']);
        // details with no parenthesised severity → omitted
        self::assertArrayNotHasKey('severity_al', $rows[2]['bind']);
    }

    #[Test]
    public function allergyInsertNormalisesSynonymsToRoundTripBuckets(): void
    {
        // Free-form LLM-extracted severity may use synonyms.
        // ``low`` → mild; ``high`` / ``life-threatening`` → severe.
        $captured = [];
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$captured): int {
                $captured[] = $bind;
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Bee stings',
                    details: 'wheal (LOW)',
                ),
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Iodine',
                    details: 'rash (HIGH)',
                ),
                new PromotionItem(
                    kind: 'allergy',
                    title: 'Shellfish',
                    details: 'anaphylaxis (life-threatening)',
                ),
            ],
        );

        self::assertSame('mild', $captured[0]['severity_al']);
        self::assertSame('severe', $captured[1]['severity_al']);
        self::assertSame('severe', $captured[2]['severity_al']);
    }

    #[Test]
    public function nonAllergyKindsKeepMinimalShapeInsert(): void
    {
        // The medical_problem / medication / family_history paths
        // intentionally retain the original 8-column shape; only
        // allergy gets the diagnosis + severity_al treatment.
        $rows = [];
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$rows): int {
                $rows[] = ['sql' => $sql, 'bind' => $bind];
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [
                new PromotionItem(kind: 'medical_problem', title: 'Hypertension'),
                new PromotionItem(
                    kind: 'medication',
                    title: 'Metformin',
                    details: '500mg / bid',
                ),
                new PromotionItem(
                    kind: 'family_history',
                    title: 'Mother: hypertension',
                ),
            ],
        );

        foreach ($rows as $row) {
            self::assertArrayNotHasKey('diagnosis', $row['bind']);
            self::assertArrayNotHasKey('severity_al', $row['bind']);
            self::assertStringNotContainsString('diagnosis', $row['sql']);
            self::assertStringNotContainsString('severity_al', $row['sql']);
        }
    }

    #[Test]
    public function lineageHintIsOmittedWhenNeitherQrNorDocumentIdIsPresent(): void
    {
        $captured = null;
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$captured): int {
                $captured = $bind;
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'u',
            questionnaireResponseId: null,
            documentId: null,
            items: [new PromotionItem(kind: 'allergy', title: 'Latex')],
        );

        // Comments still carry the source attribution but no
        // qr_id/doc_id parens.
        self::assertStringContainsString('Imported from AgentForge intake form', $captured['comments']);
        self::assertStringNotContainsString('qr_id=', $captured['comments']);
        self::assertStringNotContainsString('doc_id=', $captured['comments']);
    }

    #[Test]
    public function propagatesExceptionWhenAnyRowFails(): void
    {
        // The DBAL transactional() wrapper handles rollback itself
        // when the inner callback throws — the writer's contract is
        // simply "if any executeStatement throws, the call propagates
        // and the partial state is undone by transactional()". We
        // verify here that the exception propagates AND that no
        // further executeStatement calls happen after the failure
        // (i.e. we don't keep inserting rows past a failure).
        $insertCalls = 0;
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function () use (&$insertCalls): int {
                $insertCalls++;
                if ($insertCalls === 2) {
                    throw new \RuntimeException('synthetic insert failure');
                }
                return 1;
            },
        );
        $connection->method('lastInsertId')->willReturn('1');

        $writer = new IntakePromotionWriter($connection);

        $caught = null;
        try {
            $writer->persist(
                patientId: 1,
                username: 'u',
                questionnaireResponseId: null,
                documentId: null,
                items: [
                    new PromotionItem(kind: 'allergy', title: 'a'),
                    new PromotionItem(kind: 'allergy', title: 'b'),
                    new PromotionItem(kind: 'allergy', title: 'c'),
                ],
            );
        } catch (\RuntimeException $e) {
            $caught = $e;
        }

        self::assertNotNull($caught);
        // Only the first two insert attempts ran (the third was
        // never reached because the callable bailed out).
        self::assertSame(2, $insertCalls);
    }

    #[Test]
    public function throwsWhenLastInsertIdIsNonPositive(): void
    {
        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturn(1);
        $connection->method('lastInsertId')->willReturn('0');

        $writer = new IntakePromotionWriter($connection);

        $this->expectException(\RuntimeException::class);
        $writer->persist(
            patientId: 1,
            username: 'u',
            questionnaireResponseId: null,
            documentId: null,
            items: [new PromotionItem(kind: 'allergy', title: 'x')],
        );
    }
}
