import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type {
  LabExtraction,
  LabExtractionValue,
} from '@/composables/parseLabExtraction'

import LabPanel from '../LabPanel.vue'

// Stub vue-router — LabPanel reads `route.params.id` to build the
// document-fetch URL. The component-level concern under test here is
// rendering and the modal toggle; routing is out of scope.
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'patient-uuid-fixture' } }),
}))

function makeBbox(overrides: Partial<{
  page: number
  bbox_confidence: number
}> = {}) {
  return {
    page: 1,
    x0: 0.1,
    y0: 0.2,
    x1: 0.4,
    y1: 0.3,
    bbox_confidence: 0.92,
    ...overrides,
  }
}

function makeCitation(withBbox = true) {
  const base = {
    sourceType: 'lab_pdf',
    sourceId: 'doc-1',
    pageOrSection: 'page 1',
    fieldOrChunkId: 'value-0',
    quoteOrValue: 'HbA1c 6.7 %',
  }
  return withBbox ? { ...base, pageBbox: makeBbox() } : base
}

function makeValue(
  overrides: Partial<LabExtractionValue> = {},
): LabExtractionValue {
  return {
    testName: 'HbA1c',
    value: '6.7',
    abnormalFlag: 'high',
    citation: makeCitation(true),
    ...overrides,
  } as LabExtractionValue
}

function makeBaseExtraction(
  overrides: Partial<LabExtraction> = {},
): LabExtraction {
  return {
    documentId: 7,
    patientId: 42,
    extractionConfidence: 0.85,
    values: [],
    unsupportedFields: [],
    ...overrides,
  } as LabExtraction
}

describe('LabPanel', () => {
  it('renders a row per lab value with test name, value, unit, and reference', () => {
    const ext = makeBaseExtraction({
      values: [
        makeValue({
          testName: 'HbA1c',
          value: '6.7',
          unit: '%',
          referenceRange: '<5.7',
        }),
        makeValue({
          testName: 'Glucose',
          value: '92',
          unit: 'mg/dL',
          referenceRange: '70-99',
          abnormalFlag: 'normal',
        }),
      ],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('HbA1c')
    expect(text).toContain('6.7')
    expect(text).toContain('%')
    expect(text).toContain('<5.7')
    expect(text).toContain('Glucose')
    expect(text).toContain('92')
    expect(text).toContain('mg/dL')
    expect(text).toContain('70-99')
  })

  it('shows the LOINC code when present', () => {
    const ext = makeBaseExtraction({
      values: [
        makeValue({ loincCode: '4548-4' }),
      ],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    expect(wrapper.text()).toContain('LOINC 4548-4')
  })

  it('renders abnormal-flag pills with display labels for the closed set', () => {
    const ext = makeBaseExtraction({
      values: [
        makeValue({ testName: 'A', abnormalFlag: 'critical_high' }),
        makeValue({ testName: 'B', abnormalFlag: 'critical_low' }),
        makeValue({ testName: 'C', abnormalFlag: 'normal' }),
      ],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Critical high')
    expect(text).toContain('Critical low')
    expect(text).toContain('Normal')
  })

  it('renders header meta (ordering provider, accession) when present', () => {
    const ext = makeBaseExtraction({
      orderingProvider: 'Dr. Smith',
      accessionNumber: 'A-7421',
      values: [makeValue()],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Dr. Smith')
    expect(text).toContain('A-7421')
  })

  it('renders the empty-state line when no values were extracted', () => {
    const wrapper = mount(LabPanel, {
      props: { extraction: makeBaseExtraction() },
    })
    expect(wrapper.text()).toContain('No lab values were extracted')
  })

  it('renders the extraction confidence as a percentage', () => {
    const ext = makeBaseExtraction({ extractionConfidence: 0.72 })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    expect(wrapper.text()).toContain('72%')
  })

  it('shows the bbox count on the View source button when bboxes are present', () => {
    const ext = makeBaseExtraction({
      values: [
        makeValue({ testName: 'A' }),
        makeValue({ testName: 'B' }),
        makeValue({ testName: 'C' }),
      ],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const button = wrapper.find('button[aria-label*="View source"]')
    expect(button.exists()).toBe(true)
    expect(button.text()).toMatch(/View source\s*\(3\)/)
  })

  it('renders just "View source" (no count) when no values carry a bbox', () => {
    const ext = makeBaseExtraction({
      values: [
        makeValue({ citation: makeCitation(false) }),
      ],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const button = wrapper.find('button[aria-label*="View source"]')
    expect(button.exists()).toBe(true)
    expect(button.text()).not.toMatch(/\(\d+\)/)
  })

  it('opens the source-document modal when View source is clicked', async () => {
    const ext = makeBaseExtraction({
      values: [makeValue()],
    })
    const wrapper = mount(LabPanel, {
      props: { extraction: ext },
      global: {
        // BaseModal renders into a teleport; in jsdom we want the body
        // node addressable. Stub it as a simple `<div>` so the spec can
        // assert on the modal's contents being mounted.
        stubs: {
          BaseModal: {
            props: ['open', 'title', 'size'],
            template:
              '<div data-testid="modal-stub" v-if="open"><slot /></div>',
          },
          DocumentViewer: {
            props: ['src', 'bboxes'],
            template:
              '<div data-testid="document-viewer-stub" :data-bbox-count="bboxes.length" />',
          },
        },
      },
    })
    expect(wrapper.find('[data-testid="modal-stub"]').exists()).toBe(false)
    await wrapper.find('button[aria-label*="View source"]').trigger('click')
    const modal = wrapper.find('[data-testid="modal-stub"]')
    expect(modal.exists()).toBe(true)
    const viewer = wrapper.find('[data-testid="document-viewer-stub"]')
    expect(viewer.exists()).toBe(true)
    expect(viewer.attributes('data-bbox-count')).toBe('1')
  })

  it('surfaces unsupported_fields as a "needs review" callout', () => {
    const ext = makeBaseExtraction({
      values: [makeValue()],
      unsupportedFields: ['handwritten units', 'illegible flag'],
    })
    const wrapper = mount(LabPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Needs your review')
    expect(text).toContain('handwritten units')
    expect(text).toContain('illegible flag')
  })
})
