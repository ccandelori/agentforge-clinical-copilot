import { describe, expect, it } from 'vitest'

import {
  parseIntakeExtraction,
  type IntakeExtraction,
} from './parseIntakeExtraction'

/**
 * Defensive parser for the opaque `extraction` dict the sidecar surfaces
 * on AgentTurnResponse. The sidecar serializes its `IntakeFormExtraction`
 * Pydantic model via `model_dump(mode="json")`; this parser only honours
 * the fields the drawer renders. Unknown fields are tolerated; required
 * fields missing → null.
 */

const VALID_CITATION = {
  source_type: 'intake_form',
  source_id: 'doc-123',
  page_or_section: 'page 2',
  evidence_text: 'Chief concern: knee pain',
}

describe('parseIntakeExtraction', () => {
  it('returns null when input is not an object', () => {
    expect(parseIntakeExtraction(null)).toBeNull()
    expect(parseIntakeExtraction(undefined)).toBeNull()
    expect(parseIntakeExtraction('string')).toBeNull()
    expect(parseIntakeExtraction(42)).toBeNull()
    expect(parseIntakeExtraction([])).toBeNull()
  })

  it('returns null when document_id or patient_id is missing', () => {
    expect(parseIntakeExtraction({ patient_id: 1 })).toBeNull()
    expect(parseIntakeExtraction({ document_id: 1 })).toBeNull()
  })

  it('returns null when extraction_confidence is out of range', () => {
    const base = {
      document_id: 1,
      patient_id: 2,
      extraction_confidence: 1.5,
    }
    expect(parseIntakeExtraction(base)).toBeNull()
  })

  it('parses a minimal-but-valid blank extraction', () => {
    const raw = {
      document_id: 7,
      patient_id: 42,
      extraction_confidence: 0.0,
      demographics: [],
      medications: [],
      allergies: [],
      family_history: [],
      unsupported_fields: [],
    }
    const out = parseIntakeExtraction(raw) as IntakeExtraction
    expect(out).not.toBeNull()
    expect(out.documentId).toBe(7)
    expect(out.patientId).toBe(42)
    expect(out.chiefConcern).toBeUndefined()
    expect(out.demographics).toEqual([])
    expect(out.medications).toEqual([])
    expect(out.allergies).toEqual([])
    expect(out.familyHistory).toEqual([])
    expect(out.unsupportedFields).toEqual([])
    expect(out.extractionConfidence).toBe(0.0)
  })

  it('parses chief_concern with its citation', () => {
    const raw = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.82,
      chief_concern: 'Persistent knee pain after a fall',
      chief_concern_citation: VALID_CITATION,
    }
    const out = parseIntakeExtraction(raw) as IntakeExtraction
    expect(out.chiefConcern).toBe('Persistent knee pain after a fall')
    expect(out.chiefConcernCitation?.sourceId).toBe('doc-123')
    expect(out.chiefConcernCitation?.evidenceText).toBe(
      'Chief concern: knee pain',
    )
  })

  it('parses a medication entry with optional dose/frequency missing', () => {
    const raw = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.8,
      medications: [{ name: 'Metformin', citation: VALID_CITATION }],
    }
    const out = parseIntakeExtraction(raw) as IntakeExtraction
    expect(out.medications).toHaveLength(1)
    const med = out.medications[0]!
    expect(med.name).toBe('Metformin')
    expect(med.dose).toBeUndefined()
    expect(med.frequency).toBeUndefined()
  })

  it('drops malformed list entries instead of failing the whole parse', () => {
    const raw = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.7,
      allergies: [
        { substance: 'Penicillin', citation: VALID_CITATION },
        { substance: 42, citation: VALID_CITATION }, // wrong type
        { citation: VALID_CITATION }, // missing substance
      ],
    }
    const out = parseIntakeExtraction(raw) as IntakeExtraction
    expect(out.allergies).toHaveLength(1)
    expect(out.allergies[0]?.substance).toBe('Penicillin')
  })

  it('preserves unsupported_fields', () => {
    const raw = {
      document_id: 1,
      patient_id: 1,
      extraction_confidence: 0.6,
      unsupported_fields: ['handwritten DOB', 'illegible insurance card'],
    }
    const out = parseIntakeExtraction(raw) as IntakeExtraction
    expect(out.unsupportedFields).toEqual([
      'handwritten DOB',
      'illegible insurance card',
    ])
  })
})
