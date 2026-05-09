import { describe, expect, it } from 'vitest'

import { parseLabExtraction, type LabExtraction } from './parseLabExtraction'

/**
 * Defensive parser for the opaque `extraction` dict the sidecar surfaces
 * when the W2 graph routed a turn through the lab-PDF contract. The
 * sidecar serializes its `LabPdfExtraction` Pydantic model via
 * `model_dump(mode="json")`. The parser claims payloads that look like
 * lab snapshots (have a `values` list, no intake-only list keys) and
 * returns `null` for everything else — including intake snapshots,
 * which `parseIntakeExtraction` owns.
 */

const VALID_LAB_BBOX = {
  page: 1,
  x0: 0.1,
  y0: 0.2,
  x1: 0.4,
  y1: 0.3,
  bbox_confidence: 0.92,
}

const VALID_LAB_CITATION = {
  source_type: 'lab_pdf',
  source_id: 'doc-123',
  page_or_section: 'page 1',
  field_or_chunk_id: 'value-0',
  quote_or_value: 'HbA1c 6.7 %',
  page_bbox: VALID_LAB_BBOX,
}

function makeRawLab(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    document_id: 7,
    patient_id: 42,
    extraction_confidence: 0.88,
    values: [
      {
        test_name: 'HbA1c',
        loinc_code: '4548-4',
        value: '6.7',
        unit: '%',
        reference_range: '<5.7',
        collection_date: '2026-04-30',
        abnormal_flag: 'high',
        citation: VALID_LAB_CITATION,
      },
    ],
    unsupported_fields: [],
    ...overrides,
  }
}

describe('parseLabExtraction', () => {
  it('returns null when input is not an object', () => {
    expect(parseLabExtraction(null)).toBeNull()
    expect(parseLabExtraction(undefined)).toBeNull()
    expect(parseLabExtraction('string')).toBeNull()
    expect(parseLabExtraction(42)).toBeNull()
    expect(parseLabExtraction([])).toBeNull()
  })

  it('returns null when the payload has no `values` array', () => {
    // Required field missing → no parse. Distinguishes us from intake.
    const raw = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.8,
    }
    expect(parseLabExtraction(raw)).toBeNull()
  })

  it('returns null on intake-shaped payloads (discriminator)', () => {
    // Belt-and-braces against the discriminator bug fixed in P1.2:
    // even if a future intake snapshot accidentally grows a `values`
    // key, the presence of any intake-only list flips us back to null
    // so the intake parser can claim it.
    const intakeShape = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.7,
      demographics: [],
      medications: [],
      allergies: [],
      family_history: [],
      chief_concern: 'knee pain',
    }
    expect(parseLabExtraction(intakeShape)).toBeNull()
  })

  it('returns null when document_id or patient_id is missing', () => {
    expect(parseLabExtraction({ values: [] })).toBeNull()
    expect(
      parseLabExtraction({
        values: [],
        document_id: 1,
        extraction_confidence: 0.5,
      }),
    ).toBeNull()
    expect(
      parseLabExtraction({
        values: [],
        patient_id: 1,
        extraction_confidence: 0.5,
      }),
    ).toBeNull()
  })

  it('returns null when extraction_confidence is out of range', () => {
    expect(
      parseLabExtraction({
        values: [],
        document_id: 1,
        patient_id: 1,
        extraction_confidence: 1.5,
      }),
    ).toBeNull()
  })

  it('parses a happy-path lab payload with one row', () => {
    const out = parseLabExtraction(makeRawLab()) as LabExtraction
    expect(out).not.toBeNull()
    expect(out.documentId).toBe(7)
    expect(out.patientId).toBe(42)
    expect(out.extractionConfidence).toBeCloseTo(0.88)
    expect(out.values).toHaveLength(1)
    const v = out.values[0]!
    expect(v.testName).toBe('HbA1c')
    expect(v.loincCode).toBe('4548-4')
    expect(v.value).toBe('6.7')
    expect(v.unit).toBe('%')
    expect(v.referenceRange).toBe('<5.7')
    expect(v.collectionDate).toBe('2026-04-30')
    expect(v.abnormalFlag).toBe('high')
    expect(v.citation.sourceType).toBe('lab_pdf')
    expect(v.citation.pageBbox).toEqual(VALID_LAB_BBOX)
  })

  it('parses an empty values list as a valid (zero-row) extraction', () => {
    const out = parseLabExtraction(
      makeRawLab({ values: [] }),
    ) as LabExtraction
    expect(out).not.toBeNull()
    expect(out.values).toEqual([])
  })

  it('omits optional top-level fields when absent on the wire', () => {
    const out = parseLabExtraction(makeRawLab()) as LabExtraction
    expect(out.orderingProvider).toBeUndefined()
    expect(out.accessionNumber).toBeUndefined()
  })

  it('parses optional ordering_provider and accession_number when present', () => {
    const out = parseLabExtraction(
      makeRawLab({
        ordering_provider: 'Dr. Smith',
        accession_number: 'A-7421',
      }),
    ) as LabExtraction
    expect(out.orderingProvider).toBe('Dr. Smith')
    expect(out.accessionNumber).toBe('A-7421')
  })

  it('drops malformed value rows but keeps the parse', () => {
    const out = parseLabExtraction(
      makeRawLab({
        values: [
          {
            test_name: 'Glucose',
            value: '92',
            citation: VALID_LAB_CITATION,
          },
          { value: '92', citation: VALID_LAB_CITATION }, // missing test_name
          { test_name: 'X', citation: VALID_LAB_CITATION }, // missing value
          { test_name: 'Y', value: 'z' }, // missing citation
        ],
      }),
    ) as LabExtraction
    expect(out.values).toHaveLength(1)
    expect(out.values[0]?.testName).toBe('Glucose')
  })

  it('omits optional per-row fields when absent', () => {
    const out = parseLabExtraction(
      makeRawLab({
        values: [
          {
            test_name: 'WBC',
            value: '6.2',
            citation: VALID_LAB_CITATION,
          },
        ],
      }),
    ) as LabExtraction
    const v = out.values[0]!
    expect(v.loincCode).toBeUndefined()
    expect(v.unit).toBeUndefined()
    expect(v.referenceRange).toBeUndefined()
    expect(v.collectionDate).toBeUndefined()
    // abnormal_flag defaults to 'unknown' on the sidecar; matches here.
    expect(v.abnormalFlag).toBe('unknown')
  })

  it('normalises an unknown abnormal_flag string to "unknown" rather than dropping the row', () => {
    const out = parseLabExtraction(
      makeRawLab({
        values: [
          {
            test_name: 'WBC',
            value: '6.2',
            abnormal_flag: 'HIGH-ish', // not in the closed set
            citation: VALID_LAB_CITATION,
          },
        ],
      }),
    ) as LabExtraction
    expect(out.values).toHaveLength(1)
    expect(out.values[0]?.abnormalFlag).toBe('unknown')
  })

  it('drops malformed page_bbox without dropping the citation', () => {
    const inverted = {
      page: 1,
      x0: 0.5,
      y0: 0.5,
      x1: 0.4,
      y1: 0.4,
      bbox_confidence: 0.9,
    }
    const out = parseLabExtraction(
      makeRawLab({
        values: [
          {
            test_name: 'WBC',
            value: '6.2',
            citation: { ...VALID_LAB_CITATION, page_bbox: inverted },
          },
        ],
      }),
    ) as LabExtraction
    const v = out.values[0]!
    expect(v.citation.pageBbox).toBeUndefined()
    expect(v.citation.quoteOrValue).toBe('HbA1c 6.7 %')
  })

  it('preserves unsupported_fields', () => {
    const out = parseLabExtraction(
      makeRawLab({
        unsupported_fields: ['handwritten units', 'illegible flag'],
      }),
    ) as LabExtraction
    expect(out.unsupportedFields).toEqual([
      'handwritten units',
      'illegible flag',
    ])
  })
})
