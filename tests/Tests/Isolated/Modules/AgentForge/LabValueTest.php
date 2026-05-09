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

use DomainException;
use OpenEMR\Modules\AgentForge\Domain\LabValue;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;

/**
 * Unit tests for the LabValue domain primitive — the parse-don't-validate
 * boundary between the lab persistence controller and LabResultWriter.
 *
 * Constructor invariants (test_name and value are non-empty strings)
 * carry the load here: if either fails the writer must never see the
 * row, otherwise empty strings flow into procedure_result and silently
 * corrupt the patient's lab record.
 */
final class LabValueTest extends TestCase
{
    #[Test]
    public function constructsWithRequiredFields(): void
    {
        $value = new LabValue(testName: 'Glucose', value: '180');
        self::assertSame('Glucose', $value->testName);
        self::assertSame('180', $value->value);
        self::assertNull($value->loincCode);
        self::assertNull($value->unit);
        self::assertNull($value->referenceRange);
        self::assertNull($value->collectionDate);
        self::assertNull($value->abnormalFlag);
    }

    #[Test]
    public function constructorRejectsEmptyTestName(): void
    {
        self::expectException(DomainException::class);
        new LabValue(testName: '', value: '180');
    }

    #[Test]
    public function constructorRejectsEmptyValue(): void
    {
        self::expectException(DomainException::class);
        new LabValue(testName: 'Glucose', value: '');
    }

    #[Test]
    public function fromMixedReturnsLabValueWhenWellFormed(): void
    {
        $value = LabValue::fromMixed([
            'test_name' => 'Glucose',
            'value' => '180',
            'unit' => 'mg/dL',
            'loinc_code' => '2345-7',
            'reference_range' => '70-99',
            'collection_date' => '2026-04-30',
            'abnormal_flag' => 'high',
        ]);

        self::assertSame('Glucose', $value->testName);
        self::assertSame('180', $value->value);
        self::assertSame('mg/dL', $value->unit);
        self::assertSame('2345-7', $value->loincCode);
        self::assertSame('70-99', $value->referenceRange);
        self::assertSame('2026-04-30', $value->collectionDate);
        self::assertSame('high', $value->abnormalFlag);
    }

    #[Test]
    public function fromMixedRejectsNonArrayEntry(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed('not an array');
    }

    #[Test]
    public function fromMixedRejectsMissingTestName(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['value' => '180']);
    }

    #[Test]
    public function fromMixedRejectsMissingValue(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['test_name' => 'Glucose']);
    }

    #[Test]
    public function fromMixedRejectsNonStringTestName(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['test_name' => 42, 'value' => '180']);
    }

    #[Test]
    public function fromMixedRejectsEmptyStringTestName(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['test_name' => '', 'value' => '180']);
    }

    #[Test]
    public function fromMixedRejectsEmptyStringValue(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['test_name' => 'Glucose', 'value' => '']);
    }

    #[Test]
    public function fromMixedRejectsNullValue(): void
    {
        self::expectException(DomainException::class);
        LabValue::fromMixed(['test_name' => 'Glucose', 'value' => null]);
    }

    #[Test]
    public function fromMixedTreatsBlankOptionalsAsNull(): void
    {
        // An empty-string optional collapses to null — there's no
        // information in '' that the writer can act on, and treating
        // them uniformly avoids a "did the source send '' or omit
        // it?" branch downstream.
        $value = LabValue::fromMixed([
            'test_name' => 'Glucose',
            'value' => '180',
            'unit' => '',
            'loinc_code' => '',
        ]);
        self::assertNull($value->unit);
        self::assertNull($value->loincCode);
    }

    #[Test]
    public function fromMixedIgnoresNonStringOptionals(): void
    {
        // Non-string optional fields collapse to null rather than
        // raising — they're presentation-side and the writer can
        // tolerate their absence.
        $value = LabValue::fromMixed([
            'test_name' => 'Glucose',
            'value' => '180',
            'unit' => 99,
        ]);
        self::assertNull($value->unit);
    }
}
