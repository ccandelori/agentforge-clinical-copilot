import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { IntakeExtraction } from '@/composables/useAgentTurn'

import ExtractionPanel from '../ExtractionPanel.vue'

// Stub vue-router — ExtractionPanel reads route.params.id to build the
// document-fetch URL. The component-level concern under test here is
// rendering, not routing; the source-modal interaction belongs in an
// integration test (out of scope for this spec).
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'patient-uuid-fixture' } }),
}))

function makeCitation() {
  return {
    sourceType: 'intake_form',
    sourceId: 'doc-1',
    pageOrSection: 'page 2',
    evidenceText: 'Patient reports knee pain since fall',
  }
}

function makeBaseExtraction(overrides: Partial<IntakeExtraction> = {}) {
  return {
    documentId: 7,
    patientId: 42,
    extractionConfidence: 0.85,
    demographics: [],
    medications: [],
    allergies: [],
    familyHistory: [],
    unsupportedFields: [],
    ...overrides,
  } as IntakeExtraction
}

describe('ExtractionPanel', () => {
  it('renders the chief concern with its citation excerpt when present', () => {
    const ext = makeBaseExtraction({
      chiefConcern: 'Persistent knee pain',
      chiefConcernCitation: makeCitation(),
    })

    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Persistent knee pain')
    expect(text).toContain('Patient reports knee pain since fall')
  })

  it('lists each medication with optional dose visible when present', () => {
    const ext = makeBaseExtraction({
      medications: [
        { name: 'Metformin', dose: '500mg', citation: makeCitation() },
        { name: 'Lisinopril', citation: makeCitation() },
      ],
    })

    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Metformin')
    expect(text).toContain('500mg')
    expect(text).toContain('Lisinopril')
  })

  it('surfaces unsupported_fields as a "needs review" callout', () => {
    const ext = makeBaseExtraction({
      unsupportedFields: ['handwritten DOB', 'illegible insurance card'],
    })

    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('handwritten DOB')
    expect(text).toContain('illegible insurance card')
  })

  it('hides empty sections rather than rendering blank rows', () => {
    const ext = makeBaseExtraction()
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).not.toContain('Medications')
    expect(text).not.toContain('Allergies')
    expect(text).not.toContain('Family history')
  })

  it('renders the extraction confidence as a percentage', () => {
    const ext = makeBaseExtraction({ extractionConfidence: 0.72 })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    expect(wrapper.text()).toContain('72%')
  })
})
