/**
 * Defensive parser for the sidecar's intake-form extraction snapshot.
 *
 * The sidecar serialises its `IntakeFormExtraction` Pydantic model via
 * `model_dump(mode="json")` and surfaces it as an opaque dict on
 * `AgentTurnResponse.extraction`. This module is the type-safe view of
 * that dict for the drawer UI.
 *
 * Field names are camelCase on the TS side (Vue convention); the parser
 * does the snake-case → camelCase mapping at the boundary so the rest of
 * the codebase doesn't carry the underscore noise. Unknown fields are
 * tolerated; structurally-invalid required fields → null parse result.
 */

export interface IntakeExtractionCitation {
  readonly sourceType: string
  readonly sourceId: string
  readonly pageOrSection: string
  readonly evidenceText: string
}

export interface IntakeExtractionDemographic {
  readonly field: string
  readonly value: string
  readonly citation: IntakeExtractionCitation
}

export interface IntakeExtractionMedication {
  readonly name: string
  readonly dose?: string
  readonly frequency?: string
  readonly citation: IntakeExtractionCitation
}

export interface IntakeExtractionAllergy {
  readonly substance: string
  readonly reaction?: string
  readonly severity?: string
  readonly citation: IntakeExtractionCitation
}

export interface IntakeExtractionFamilyHistory {
  readonly relative: string
  readonly condition: string
  readonly citation: IntakeExtractionCitation
}

export interface IntakeExtraction {
  readonly documentId: number
  readonly patientId: number
  readonly chiefConcern?: string
  readonly chiefConcernCitation?: IntakeExtractionCitation
  readonly demographics: readonly IntakeExtractionDemographic[]
  readonly medications: readonly IntakeExtractionMedication[]
  readonly allergies: readonly IntakeExtractionAllergy[]
  readonly familyHistory: readonly IntakeExtractionFamilyHistory[]
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

function parseCitation(v: unknown): IntakeExtractionCitation | null {
  if (!isObject(v)) return null
  const sourceType = parseString(v.source_type)
  const sourceId = parseString(v.source_id)
  const pageOrSection = parseString(v.page_or_section)
  const evidenceText = parseString(v.evidence_text)
  if (
    sourceType === undefined
    || sourceId === undefined
    || pageOrSection === undefined
    || evidenceText === undefined
  ) {
    return null
  }
  return { sourceType, sourceId, pageOrSection, evidenceText }
}

function parseDemographic(v: unknown): IntakeExtractionDemographic | null {
  if (!isObject(v)) return null
  const field = parseString(v.field)
  const value = parseString(v.value)
  const citation = parseCitation(v.citation)
  if (field === undefined || value === undefined || citation === null) {
    return null
  }
  return { field, value, citation }
}

function parseMedication(v: unknown): IntakeExtractionMedication | null {
  if (!isObject(v)) return null
  const name = parseString(v.name)
  const citation = parseCitation(v.citation)
  if (name === undefined || citation === null) return null
  const out: IntakeExtractionMedication = {
    name,
    citation,
    ...(parseString(v.dose) !== undefined ? { dose: parseString(v.dose)! } : {}),
    ...(parseString(v.frequency) !== undefined
      ? { frequency: parseString(v.frequency)! }
      : {}),
  }
  return out
}

function parseAllergy(v: unknown): IntakeExtractionAllergy | null {
  if (!isObject(v)) return null
  const substance = parseString(v.substance)
  const citation = parseCitation(v.citation)
  if (substance === undefined || citation === null) return null
  const out: IntakeExtractionAllergy = {
    substance,
    citation,
    ...(parseString(v.reaction) !== undefined
      ? { reaction: parseString(v.reaction)! }
      : {}),
    ...(parseString(v.severity) !== undefined
      ? { severity: parseString(v.severity)! }
      : {}),
  }
  return out
}

function parseFamilyHistory(
  v: unknown,
): IntakeExtractionFamilyHistory | null {
  if (!isObject(v)) return null
  const relative = parseString(v.relative)
  const condition = parseString(v.condition)
  const citation = parseCitation(v.citation)
  if (
    relative === undefined
    || condition === undefined
    || citation === null
  ) {
    return null
  }
  return { relative, condition, citation }
}

function parseList<T>(
  v: unknown,
  itemParser: (raw: unknown) => T | null,
): readonly T[] {
  if (!Array.isArray(v)) return []
  const out: T[] = []
  for (const item of v) {
    const parsed = itemParser(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

export function parseIntakeExtraction(raw: unknown): IntakeExtraction | null {
  if (!isObject(raw)) return null

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

  const chiefConcern = parseString(raw.chief_concern)
  const chiefConcernCitation = parseCitation(raw.chief_concern_citation)

  return {
    documentId,
    patientId,
    extractionConfidence: conf,
    ...(chiefConcern !== undefined ? { chiefConcern } : {}),
    ...(chiefConcernCitation !== null
      ? { chiefConcernCitation }
      : {}),
    demographics: parseList(raw.demographics, parseDemographic),
    medications: parseList(raw.medications, parseMedication),
    allergies: parseList(raw.allergies, parseAllergy),
    familyHistory: parseList(raw.family_history, parseFamilyHistory),
    unsupportedFields: parseStringList(raw.unsupported_fields),
  }
}
