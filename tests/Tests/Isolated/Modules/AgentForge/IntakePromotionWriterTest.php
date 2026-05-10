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

        $connection = self::createMock(Connection::class);
        $connection->method('transactional')->willReturnCallback(
            static fn (callable $fn) => $fn(),
        );
        $connection->method('executeStatement')->willReturnCallback(
            static function (string $sql, array $bind) use (&$captured): int {
                $captured[] = ['sql' => $sql, 'bind' => $bind];
                return 1;
            },
        );
        // Note: ``willReturnCallback`` with a closed-over ``++$counter``
        // is brittle on PHP 8.5 + PHPUnit 11 — the closure can be
        // re-invoked with stale state on the second call. The explicit
        // consecutive-returns API gives each insert a distinct
        // synthetic id without relying on reference-increment semantics.
        $connection->method('lastInsertId')
            ->willReturnOnConsecutiveCalls('101', '102');

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
    public function nonAllergyListsKindsKeepMinimalShapeInsert(): void
    {
        // The medical_problem / family_history paths intentionally
        // retain the original 8-column ``lists`` shape; only allergy
        // gets the diagnosis + severity_al treatment, and medication
        // routes to ``prescriptions`` instead (covered separately).
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
                    kind: 'family_history',
                    title: 'Mother: hypertension',
                ),
            ],
        );

        foreach ($rows as $row) {
            self::assertStringContainsString('INSERT INTO lists', $row['sql']);
            self::assertArrayNotHasKey('diagnosis', $row['bind']);
            self::assertArrayNotHasKey('severity_al', $row['bind']);
            self::assertStringNotContainsString('diagnosis', $row['sql']);
            self::assertStringNotContainsString('severity_al', $row['sql']);
        }
    }

    #[Test]
    public function medicationKindRoutesToPrescriptionsTable(): void
    {
        // The dashboard's MedicationsCard reads from FHIR
        // MedicationRequest, which is projected from ``prescriptions``
        // (not ``lists``). The writer must therefore branch
        // ``kind='medication'`` items to a prescriptions INSERT.
        // Critically: it must NOT also write a ``lists`` row — single
        // source of truth, no shadow rows in the chart-summary list.
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
        $connection->method('lastInsertId')->willReturn('501');

        $writer = new IntakePromotionWriter($connection);
        $result = $writer->persist(
            patientId: 42,
            username: 'admin',
            questionnaireResponseId: 'qr-9',
            documentId: 17,
            items: [new PromotionItem(
                kind: 'medication',
                title: 'Lisinopril',
                details: '10 mg PO daily',
            )],
        );

        self::assertNotNull($captured);
        // Hitting the prescriptions table (not lists).
        self::assertStringContainsString('INSERT INTO prescriptions', $captured['sql']);
        self::assertStringNotContainsString('INSERT INTO lists', $captured['sql']);

        // Core column population: pid, drug name, parsed dose,
        // full-sig text. ``active=1`` is hardcoded into the SQL since
        // it's a literal, not a bind.
        self::assertSame(42, $captured['bind']['pid']);
        self::assertSame('Lisinopril', $captured['bind']['drug']);
        self::assertSame('10 mg', $captured['bind']['dosage']);
        self::assertSame('10 mg PO daily', $captured['bind']['sig']);
        self::assertSame('admin', $captured['bind']['user']);
        self::assertSame('community', $captured['bind']['category']);
        self::assertSame('order', $captured['bind']['intent']);

        // Comments-channel lineage hint — the chart row has a
        // breadcrumb back to the source extraction, same as the
        // ``lists``-bound paths get.
        self::assertStringContainsString(
            'Imported from AgentForge intake form',
            $captured['bind']['note'],
        );
        self::assertStringContainsString('qr_id=qr-9', $captured['bind']['note']);
        self::assertStringContainsString('doc_id=17', $captured['bind']['note']);

        // UUID is binary(16) raw bytes — the writer generates one per
        // row so the FHIR projection's row addressability works
        // without depending on uuid_registry self-heal.
        self::assertIsString($captured['bind']['uuid']);
        self::assertSame(16, strlen($captured['bind']['uuid']));

        // SQL must include the NOT NULL columns that have no schema
        // default — without these, the live INSERT would crash.
        self::assertStringContainsString('txDate', $captured['sql']);
        self::assertStringContainsString('usage_category_title', $captured['sql']);
        self::assertStringContainsString('request_intent_title', $captured['sql']);
        // active=1 is critical for the FHIR status='active' chain.
        self::assertMatchesRegularExpression('/\bactive\b/', $captured['sql']);

        // Returned handle carries the prescriptions row id.
        self::assertCount(1, $result->handles);
        self::assertSame('medication', $result->handles[0]->kind);
        self::assertSame(501, $result->handles[0]->listsId);
    }

    #[Test]
    public function medicationInsertNullsDoseAndSigWhenDetailsAreEmpty(): void
    {
        // Edge case: medication item arrived with no details string.
        // The writer should still produce a valid prescriptions INSERT
        // (drug + uuid + status + the schema-required NOT NULL columns)
        // but bind NULL for the dose/sig channels rather than empty
        // strings — empty strings on dosage would still satisfy the
        // FHIR text guard but read as a "" in the legacy edit form,
        // which is misleading.
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
        $connection->method('lastInsertId')->willReturn('502');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [new PromotionItem(
                kind: 'medication',
                title: 'Vitamin D',
                details: null,
            )],
        );

        self::assertNotNull($captured);
        self::assertNull($captured['bind']['dosage']);
        self::assertNull($captured['bind']['sig']);
        // Drug name is still required and present.
        self::assertSame('Vitamin D', $captured['bind']['drug']);
    }

    #[Test]
    public function medicationInsertPreservesSigEvenWhenDoseUnparsable(): void
    {
        // When the details string carries a frequency-only or
        // unstructured value (no leading "<n><unit>" token), we leave
        // dosage NULL but still preserve the full text in the
        // ``drug_dosage_instructions`` channel — that's what the
        // dashboard's frequency line falls back to.
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
        $connection->method('lastInsertId')->willReturn('503');

        $writer = new IntakePromotionWriter($connection);
        $writer->persist(
            patientId: 1,
            username: 'admin',
            questionnaireResponseId: null,
            documentId: null,
            items: [new PromotionItem(
                kind: 'medication',
                title: 'Aspirin',
                details: 'as needed for pain',
            )],
        );

        self::assertNotNull($captured);
        self::assertNull($captured['bind']['dosage']);
        self::assertSame('as needed for pain', $captured['bind']['sig']);
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
