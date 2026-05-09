/**
 * Defensive parser for the sidecar's lab-PDF extraction snapshot.
 *
 * The sidecar serialises its `LabPdfExtraction` Pydantic model via
 * `model_dump(mode="json")` and surfaces it as an opaque dict on
 * `AgentTurnResponse.extraction`. This module is the type-safe view of
 * that dict for the dashboard's `LabPanel.vue` component.
 *
 * Field-name mapping mirrors `parseIntakeExtraction.ts`: snake_case on
 * the wire, camelCase on the TS side. The two parsers share the
 * `IntakeExtractionCitation` shape because the sidecar's
 * `Citation` schema is identical for `lab_pdf` and `intake_form`
 * source types — both carry a required `PageBBox` with
 * `bbox_confidence >= 0.7`.
 *
 * Discriminator: a parse only succeeds when the payload carries the
 * lab-shape marker (`values: list`) and lacks intake-only list keys
 * (`demographics`, `medications`, `allergies`, `family_history`). This
 * is the half of the P1.2 fix that lets the new lab panel claim lab
 * payloads; the sister tightening in `parseIntakeExtraction.ts` rejects
 * the same payload from the intake parser.
 */

import type { PageBBox } from '@/types/citation'

import type { IntakeExtractionCitation } from './parseIntakeExtraction'

export type { IntakeExtractionCitation } from './parseIntakeExtraction'

/**
 * Closed set of abnormal-flag markers the sidecar emits for a lab value.
 * Mirrors `agentforge.schemas.lab.AbnormalFlag` server-side. Unknown
 * strings on the wire normalise to `'unknown'` rather than dropping the
 * row — the value text is still useful to the clinician.
 */
export type LabAbnormalFlag =
  | 'normal'
  | 'high'
  | 'low'
  | 'critical_high'
  | 'critical_low'
  | 'unknown'

const ALLOWED_FLAGS: ReadonlySet<LabAbnormalFlag> = new Set<LabAbnormalFlag>([
  'normal',
  'high',
  'low',
  'critical_high',
  'critical_low',
  'unknown',
])

export interface LabExtractionValue {
  readonly testName: string
  readonly loincCode?: string
  /** Stringified value (preserves units-in-line, ranges, "Not Detected"). */
  readonly value: string
  readonly unit?: string
  readonly referenceRange?: string
  /** ISO date string (YYYY-MM-DD) when present. */
  readonly collectionDate?: string
  readonly abnormalFlag: LabAbnormalFlag
  readonly citation: IntakeExtractionCitation
}

export interface LabExtraction {
  readonly documentId: number
  readonly patientId: number
  readonly orderingProvider?: string
  readonly accessionNumber?: string
  readonly values: readonly LabExtractionValue[]
  readonly extractionConfidence: number
  readonly unsupportedFields: readonly string[]
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function parseString(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function parseStringList(v: unknown): readonly string[] {
  if (!Array.isArray(v)) return []
  const out: string[] = []
  for (const item of v) {
    if (typeof item === 'string') out.push(item)
  }
  return out
}

function parseNumberInRange(
  v: unknown,
  min: number,
  max: number,
): number | undefined {
  if (typeof v !== 'number' || !Number.isFinite(v)) return undefined
  if (v < min || v > max) return undefined
  return v
}

function parsePageBbox(v: unknown): PageBBox | undefined {
  if (!isObject(v)) return undefined
  const page = v.page
  if (typeof page !== 'number' || !Number.isInteger(page) || page < 1) {
    return undefined
  }
  const x0 = parseNumberInRange(v.x0, 0, 1)
  const y0 = parseNumberInRange(v.y0, 0, 1)
  const x1 = parseNumberInRange(v.x1, 0, 1)
  const y1 = parseNumberInRange(v.y1, 0, 1)
  const conf = parseNumberInRange(v.bbox_confidence, 0, 1)
  if (
    x0 === undefined
    || y0 === undefined
    || x1 === undefined
    || y1 === undefined
    || conf === undefined
  ) {
    return undefined
  }
  // Mirror the schema-level invariant on the sidecar: x1 > x0 and
  // y1 > y0. The route validates and rejects inverted boxes; we
  // mirror the rule defensively here so a buggy intermediate cannot
  // sneak an inverted overlay into the renderer.
  if (x1 <= x0 || y1 <= y0) return undefined
  return { page, x0, y0, x1, y1, bbox_confidence: conf }
}

function parseCitation(v: unknown): IntakeExtractionCitation | null {
  if (!isObject(v)) return null
  const sourceType = parseString(v.source_type)
  const sourceId = parseString(v.source_id)
  const pageOrSection = parseString(v.page_or_section)
  const fieldOrChunkId = parseString(v.field_or_chunk_id)
  const quoteOrValue = parseString(v.quote_or_value)
  if (
    sourceType === undefined
    || sourceId === undefined
    || pageOrSection === undefined
    || fieldOrChunkId === undefined
    || quoteOrValue === undefined
  ) {
    return null
  }
  const pageBbox = parsePageBbox(v.page_bbox)
  return {
    sourceType,
    sourceId,
    pageOrSection,
    fieldOrChunkId,
    quoteOrValue,
    ...(pageBbox !== undefined ? { pageBbox } : {}),
  }
}

function parseAbnormalFlag(v: unknown): LabAbnormalFlag {
  if (typeof v !== 'string') return 'unknown'
  return ALLOWED_FLAGS.has(v as LabAbnormalFlag)
    ? (v as LabAbnormalFlag)
    : 'unknown'
}

function parseValue(v: unknown): LabExtractionValue | null {
  if (!isObject(v)) return null
  const testName = parseString(v.test_name)
  const value = parseString(v.value)
  const citation = parseCitation(v.citation)
  if (testName === undefined || value === undefined || citation === null) {
    return null
  }
  const loincCode = parseString(v.loinc_code)
  const unit = parseString(v.unit)
  const referenceRange = parseString(v.reference_range)
  const collectionDate = parseString(v.collection_date)
  return {
    testName,
    value,
    abnormalFlag: parseAbnormalFlag(v.abnormal_flag),
    citation,
    ...(loincCode !== undefined ? { loincCode } : {}),
    ...(unit !== undefined ? { unit } : {}),
    ...(referenceRange !== undefined ? { referenceRange } : {}),
    ...(collectionDate !== undefined ? { collectionDate } : {}),
  }
}

/**
 * Reject payloads that look like an intake extraction (P1.2 sister to
 * the intake parser's tightening). A lab payload never carries any of
 * these list keys; presence of any of them means the BFF emitted an
 * intake snapshot and we should let the intake parser claim it.
 */
function looksLikeIntake(raw: Record<string, unknown>): boolean {
  return (
    Array.isArray(raw.demographics)
    || Array.isArray(raw.medications)
    || Array.isArray(raw.allergies)
    || Array.isArray(raw.family_history)
    || typeof raw.chief_concern === 'string'
  )
}

export function parseLabExtraction(raw: unknown): LabExtraction | null {
  if (!isObject(raw)) return null

  // Discriminator — must look like a lab payload AND must NOT look like
  // an intake one. The sidecar's two extraction Pydantic models have
  // disjoint shapes (`values[]` is lab-only; `demographics[]` etc are
  // intake-only); a valid `model_dump(mode="json")` output therefore
  // satisfies exactly one branch. Belt-and-braces against any future
  // shape drift that adds an overlapping key.
  if (!Array.isArray(raw.values)) return null
  if (looksLikeIntake(raw)) return null

  const documentId = raw.document_id
  const patientId = raw.patient_id
  if (typeof documentId !== 'number' || !Number.isFinite(documentId)) {
    return null
  }
  if (typeof patientId !== 'number' || !Number.isFinite(patientId)) {
    return null
  }

  const conf = raw.extraction_confidence
  if (typeof conf !== 'number' || conf < 0 || conf > 1) {
    return null
  }

  const orderingProvider = parseString(raw.ordering_provider)
  const accessionNumber = parseString(raw.accession_number)

  const values: LabExtractionValue[] = []
  for (const item of raw.values) {
    const parsed = parseValue(item)
    if (parsed !== null) values.push(parsed)
  }

  return {
    documentId,
    patientId,
    extractionConfidence: conf,
    values,
    unsupportedFields: parseStringList(raw.unsupported_fields),
    ...(orderingProvider !== undefined ? { orderingProvider } : {}),
    ...(accessionNumber !== undefined ? { accessionNumber } : {}),
  }
}
