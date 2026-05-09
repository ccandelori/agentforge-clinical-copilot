import { describe, expect, it } from 'vitest'

import { inferDocType } from './inferDocType'

/**
 * Filename → :type:`DocumentType` heuristic. Lab markers in the filename
 * win; everything else falls back to ``intake_form`` (the demo's primary
 * path). The picker in the chat composer is doc-type-agnostic, so this
 * sniff drives the dispatch end to end. A future iteration will surface
 * an explicit doc-type select next to the attach button.
 */

describe('inferDocType', () => {
  it('infers lab_pdf when the filename contains a lab marker', () => {
    expect(inferDocType('cbc-results.pdf')).toBe('lab_pdf')
    expect(inferDocType('Patient-LIPID-panel.pdf')).toBe('lab_pdf')
    expect(inferDocType('cmp.2026-05-08.pdf')).toBe('lab_pdf')
    expect(inferDocType('hba1c.pdf')).toBe('lab_pdf')
    expect(inferDocType('Lab Results 2026.pdf')).toBe('lab_pdf')
  })

  it('falls back to intake_form for non-lab filenames', () => {
    expect(inferDocType('new-patient-intake.pdf')).toBe('intake_form')
    expect(inferDocType('history.pdf')).toBe('intake_form')
    expect(inferDocType('form.pdf')).toBe('intake_form')
  })

  it('matches lab markers as whole words, not substrings', () => {
    // ``laboratory`` should still match (``\blab\b`` substring inside
    // ``laboratory`` does not — this asserts the boundary semantics).
    expect(inferDocType('laboratory.pdf')).toBe('intake_form')
    // ``collab`` should not flip the heuristic.
    expect(inferDocType('collaboration-form.pdf')).toBe('intake_form')
  })
})
