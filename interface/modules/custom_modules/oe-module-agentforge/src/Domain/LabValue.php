<?php

/**
 * LabValue — domain primitive for one parsed lab result row from a
 * sidecar-extracted LabPdfExtraction.
 *
 * The constructor enforces the persistence-side invariants the
 * controller previously trusted the schema to provide:
 *   - test_name is a non-empty string (otherwise the procedure_result
 *     row would carry an empty result_text, silently corrupting the
 *     patient's lab list).
 *   - value is a non-empty string (same reasoning for the `result`
 *     column).
 * Optional fields (loinc_code, unit, reference_range, collection_date,
 * abnormal_flag) are kept nullable; the writer is free to fall back to
 * empty strings for those — they're presentation-side, not the
 * load-bearing identity of the result row.
 *
 * Keeping these checks inside the constructor (parse, don't validate)
 * means downstream code — LabResultWriter, audit, response payload —
 * can rely on the type system instead of re-asserting `is_string()`
 * before each substr().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\AgentForge\Domain;

use DomainException;

final readonly class LabValue
{
    public function __construct(
        public string $testName,
        public string $value,
        public ?string $loincCode = null,
        public ?string $unit = null,
        public ?string $referenceRange = null,
        public ?string $collectionDate = null,
        public ?string $abnormalFlag = null,
    ) {
        if ($testName === '') {
            throw new DomainException('LabValue.test_name must be a non-empty string');
        }
        if ($value === '') {
            throw new DomainException('LabValue.value must be a non-empty string');
        }
    }

    /**
     * Parse one LabValue from an opaque payload entry. Anything that is
     * not an associative array, or whose required string fields are
     * missing/empty/non-string, raises DomainException — the caller
     * surfaces that as HTTP 400.
     */
    public static function fromMixed(mixed $entry): self
    {
        if (!is_array($entry)) {
            throw new DomainException('LabValue entry must be an object');
        }

        $testName = self::requireNonEmptyString($entry, 'test_name');
        $value = self::requireNonEmptyString($entry, 'value');

        return new self(
            testName: $testName,
            value: $value,
            loincCode: self::optionalString($entry, 'loinc_code'),
            unit: self::optionalString($entry, 'unit'),
            referenceRange: self::optionalString($entry, 'reference_range'),
            collectionDate: self::optionalString($entry, 'collection_date'),
            abnormalFlag: self::optionalString($entry, 'abnormal_flag'),
        );
    }

    /**
     * @param array<array-key, mixed> $entry
     */
    private static function requireNonEmptyString(array $entry, string $key): string
    {
        if (!array_key_exists($key, $entry)) {
            throw new DomainException("LabValue.{$key} is required");
        }
        $raw = $entry[$key];
        if (!is_string($raw) || $raw === '') {
            throw new DomainException("LabValue.{$key} must be a non-empty string");
        }
        return $raw;
    }

    /**
     * @param array<array-key, mixed> $entry
     */
    private static function optionalString(array $entry, string $key): ?string
    {
        if (!array_key_exists($key, $entry)) {
            return null;
        }
        $raw = $entry[$key];
        if (!is_string($raw) || $raw === '') {
            return null;
        }
        return $raw;
    }
}
