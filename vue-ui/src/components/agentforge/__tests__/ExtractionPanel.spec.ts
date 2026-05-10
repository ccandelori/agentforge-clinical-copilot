import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { IntakeExtraction } from '@/composables/useAgentTurn'

import ExtractionPanel from '../ExtractionPanel.vue'

// Stub vue-router — ExtractionPanel reads route.params.id to build the
// document-fetch URL. The component-level concern under test here is
// rendering, not routing; the source-modal interaction belongs in an
// integration test (out of scope for this spec).
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'patient-uuid-fixture' } }),
}))

// Mock invalidatePatientCache so we can assert the panel hits the
// cache invalidator on a successful commit. Re-export the module
// surface so other consumers in the same suite (none today) keep
// working.
const invalidatePatientCacheMock = vi.fn()
vi.mock('@/composables/usePatient', () => ({
  invalidatePatientCache: (id: string) => invalidatePatientCacheMock(id),
}))

function makeCitation() {
  return {
    sourceType: 'intake_form',
    sourceId: 'doc-1',
    pageOrSection: 'page 2',
    fieldOrChunkId: 'chief_concern',
    quoteOrValue: 'Patient reports knee pain since fall',
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

  it('humanizes snake_case demographic field labels', () => {
    const ext = makeBaseExtraction({
      demographics: [
        { field: 'date_of_birth', value: '1980-04-12', citation: makeCitation() },
        { field: 'mrn', value: 'A-7421', citation: makeCitation() },
      ],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const text = wrapper.text()
    expect(text).toContain('Date of Birth')
    expect(text).toContain('MRN')
    expect(text).not.toContain('date_of_birth')
  })
})

// ---------------------------------------------------------------------------
// Commit-to-chart flow (Gap 2)
// ---------------------------------------------------------------------------

interface MockFetchCall {
  url: string
  init: RequestInit
}

function setupFetchMock(): {
  calls: MockFetchCall[]
  respond: (body: unknown, status?: number) => void
} {
  const calls: MockFetchCall[] = []
  let resolve: ((res: Response) => void) | null = null

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    return new Promise<Response>((res) => {
      resolve = res
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    calls,
    respond: (body, statusCode = 200) => {
      const text = typeof body === 'string' ? body : JSON.stringify(body)
      const res = new Response(text, {
        status: statusCode,
        headers: { 'Content-Type': 'application/json' },
      })
      resolve?.(res)
    },
  }
}

describe('ExtractionPanel — commit to chart', () => {
  beforeEach(() => {
    invalidatePatientCacheMock.mockClear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('hides the commit footer when there are no promotable rows', () => {
    // demographics + chief_concern alone aren't promotable; the
    // footer should stay collapsed.
    const ext = makeBaseExtraction({
      chiefConcern: 'tooth pain',
      chiefConcernCitation: makeCitation(),
      demographics: [
        { field: 'date_of_birth', value: '1980-01-01', citation: makeCitation() },
      ],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    expect(wrapper.text()).not.toContain('Commit selected to chart')
    expect(wrapper.text()).not.toContain('Select rows to commit')
  })

  it('shows a per-row checkbox checked-by-default for each promotable row', () => {
    const ext = makeBaseExtraction({
      allergies: [{ substance: 'Penicillin', citation: makeCitation() }],
      medications: [{ name: 'Metformin', citation: makeCitation() }],
      familyHistory: [{ relative: 'Mother', condition: 'Hypertension', citation: makeCitation() }],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    // 1 allergy + 1 medication + 1 family-history = 3 checkboxes.
    expect(checkboxes).toHaveLength(3)
    for (const cb of checkboxes) {
      expect((cb.element as HTMLInputElement).checked).toBe(true)
    }
  })

  it('reflects un-checked rows in the commit count', async () => {
    const ext = makeBaseExtraction({
      allergies: [
        { substance: 'Penicillin', citation: makeCitation() },
        { substance: 'Latex', citation: makeCitation() },
      ],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })
    expect(wrapper.text()).toContain('2 rows selected')

    // Uncheck the first allergy.
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    await checkboxes[0]!.setValue(false)
    expect(wrapper.text()).toContain('1 row selected')
  })

  it('POSTs the selected rows to /api/agent/promote/intake on Commit click', async () => {
    const { calls, respond } = setupFetchMock()
    const ext = makeBaseExtraction({
      allergies: [{ substance: 'Penicillin', reaction: 'rash', citation: makeCitation() }],
      medications: [{ name: 'Metformin', dose: '500mg', frequency: 'bid', citation: makeCitation() }],
      familyHistory: [{ relative: 'Mother', condition: 'Hypertension', citation: makeCitation() }],
    })
    const wrapper = mount(ExtractionPanel, {
      props: { extraction: ext, questionnaireResponseId: 'qr-1' },
    })

    const button = wrapper.find('button[aria-label*="Commit"]')
    expect(button.exists()).toBe(true)
    await button.trigger('click')

    // Wait for the in-flight fetch to be issued.
    await flushPromises()

    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/agent/promote/intake')
    const body = JSON.parse(calls[0]!.init.body as string)
    expect(body.patient_uuid).toBe('patient-uuid-fixture')
    expect(body.questionnaire_response_id).toBe('qr-1')
    expect(body.document_id).toBe('7')
    // Three checked rows: allergy + medication + family_history.
    expect(body.items).toHaveLength(3)
    expect(body.items[0]).toEqual({
      kind: 'allergy',
      title: 'Penicillin',
      details: 'rash',
    })
    expect(body.items[1]).toEqual({
      kind: 'medication',
      title: 'Metformin',
      details: '500mg / bid',
    })
    expect(body.items[2]).toEqual({
      kind: 'family_history',
      title: 'Mother: Hypertension',
    })

    // Resolve the BFF call so the success path runs.
    respond({
      count: 3,
      promoted: [
        { kind: 'allergy', lists_id: 1, title: 'Penicillin' },
        { kind: 'medication', lists_id: 2, title: 'Metformin' },
        { kind: 'family_history', lists_id: 3, title: 'Mother: Hypertension' },
      ],
    }, 201)
    await flushPromises()

    // Cache invalidation hits with the right patient id.
    expect(invalidatePatientCacheMock).toHaveBeenCalledWith('patient-uuid-fixture')

    // The panel emits 'committed' with the success count.
    const emitted = wrapper.emitted('committed')
    expect(emitted).toBeTruthy()
    expect(emitted![0]).toEqual([3])
  })

  it('marks rows as committed (dimmed + disabled) after a successful commit', async () => {
    const { respond } = setupFetchMock()
    const ext = makeBaseExtraction({
      allergies: [{ substance: 'Penicillin', citation: makeCitation() }],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })

    await wrapper.find('button[aria-label*="Commit"]').trigger('click')
    await flushPromises()
    respond({ count: 1, promoted: [{ kind: 'allergy', lists_id: 1, title: 'Penicillin' }] }, 201)
    await flushPromises()

    const checkbox = wrapper.find('input[type="checkbox"]')
    expect((checkbox.element as HTMLInputElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('committed')
  })

  it('surfaces a per-batch error message and lets the user retry on failure', async () => {
    const { respond } = setupFetchMock()
    const ext = makeBaseExtraction({
      allergies: [{ substance: 'Penicillin', citation: makeCitation() }],
    })
    const wrapper = mount(ExtractionPanel, { props: { extraction: ext } })

    await wrapper.find('button[aria-label*="Commit"]').trigger('click')
    await flushPromises()
    respond({ error: 'forbidden' }, 502)
    await flushPromises()

    // Error surfaces in the panel (role=alert).
    expect(wrapper.text()).toMatch(/HTTP 502/)
    // Checkbox stays interactive — user can retry without re-checking.
    const checkbox = wrapper.find('input[type="checkbox"]')
    expect((checkbox.element as HTMLInputElement).disabled).toBe(false)
  })
})
