import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import LabResultsCard from '@/components/LabResultsCard.vue'

interface MakeObsOptions {
  id?: string
  loincCode?: string
  name?: string
  value?: number
  valueString?: string
  valueCodeableConceptText?: string
  unit?: string
  effectiveDateTime?: string
  refLow?: number
  refHigh?: number
  interpretation?: string[]
}

function makeObs(opts: MakeObsOptions): fhir4.Observation {
  const o: fhir4.Observation = {
    resourceType: 'Observation',
    id: opts.id ?? 'obs-1',
    status: 'final',
    code: {
      coding: [
        {
          system: 'http://loinc.org',
          code: opts.loincCode ?? '6690-2',
          display: opts.name ?? 'Test analyte',
        },
      ],
      text: opts.name ?? 'Test analyte',
    },
    subject: { reference: 'Patient/p1' },
    category: [
      {
        coding: [
          {
            system: 'http://terminology.hl7.org/CodeSystem/observation-category',
            code: 'laboratory',
          },
        ],
      },
    ],
  }
  if (opts.value !== undefined) {
    o.valueQuantity = { value: opts.value }
    if (opts.unit !== undefined) o.valueQuantity.unit = opts.unit
  }
  if (opts.valueString !== undefined) o.valueString = opts.valueString
  if (opts.valueCodeableConceptText !== undefined) {
    o.valueCodeableConcept = { text: opts.valueCodeableConceptText }
  }
  if (opts.effectiveDateTime !== undefined) {
    o.effectiveDateTime = opts.effectiveDateTime
  }
  if (opts.refLow !== undefined || opts.refHigh !== undefined) {
    const range: fhir4.ObservationReferenceRange = {}
    if (opts.refLow !== undefined) range.low = { value: opts.refLow }
    if (opts.refHigh !== undefined) range.high = { value: opts.refHigh }
    o.referenceRange = [range]
  }
  if (opts.interpretation !== undefined && opts.interpretation.length > 0) {
    o.interpretation = [
      {
        coding: opts.interpretation.map((code) => ({
          system: 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
          code,
        })),
      },
    ]
  }
  return o
}

function makeBundle(observations: fhir4.Observation[]): fhir4.Bundle {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: observations.map((resource) => ({ resource })),
  }
}

function mockFetchResolved(payload: unknown): void {
  const response = {
    ok: true,
    status: 200,
    json: async () => payload,
  } as unknown as Response
  globalThis.fetch = vi
    .fn<typeof fetch>()
    .mockResolvedValue(response) as unknown as typeof fetch
}

function mockFetchRejected(error: Error): void {
  globalThis.fetch = vi
    .fn<typeof fetch>()
    .mockRejectedValue(error) as unknown as typeof fetch
}

async function mountReady(bundle: fhir4.Bundle) {
  mockFetchResolved(bundle)
  const wrapper = mount(LabResultsCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<LabResultsCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/Observation with category=laboratory', () => {
    mockFetchResolved(makeBundle([]))
    mount(LabResultsCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/Observation?patient=patient-42&category=laboratory',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the empty copy when no lab results are on file', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('No lab results on file')
  })

  it('renders one row per unique LOINC code (deduplicates same-code observations)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          loincCode: '6690-2',
          name: 'WBC',
          value: 6.9,
          unit: '10*3/uL',
          effectiveDateTime: '2024-06-01',
        }),
        makeObs({
          id: 'b',
          loincCode: '6690-2',
          name: 'WBC',
          value: 7.1,
          unit: '10*3/uL',
          effectiveDateTime: '2023-06-01',
        }),
        makeObs({
          id: 'c',
          loincCode: '789-8',
          name: 'RBC',
          value: 4.8,
          unit: '10*6/uL',
          effectiveDateTime: '2024-06-01',
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('WBC')
    expect(wrapper.text()).toContain('RBC')
  })

  it('uses the most-recent observation as the displayed value', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'old',
          loincCode: '6690-2',
          name: 'WBC',
          value: 5.0,
          unit: '10*3/uL',
          effectiveDateTime: '2020-01-01',
        }),
        makeObs({
          id: 'new',
          loincCode: '6690-2',
          name: 'WBC',
          value: 8.5,
          unit: '10*3/uL',
          effectiveDateTime: '2024-06-01',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('8.5')
    expect(wrapper.text()).not.toContain('5 10*3/uL')
  })

  it('renders valueQuantity with unit', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Glucose', value: 95, unit: 'mg/dL' }),
      ]),
    )
    expect(wrapper.text()).toContain('95')
    expect(wrapper.text()).toContain('mg/dL')
  })

  it('falls back to valueCodeableConcept.text when valueQuantity is absent', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'Cause of Death',
          valueCodeableConceptText: 'Cardiovascular disease',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Cardiovascular disease')
  })

  it('falls back to valueString when neither valueQuantity nor valueCodeableConcept is present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Color', valueString: 'Yellow' }),
      ]),
    )
    expect(wrapper.text()).toContain('Yellow')
  })

  it('skips OpenEMR template-placeholder valueString bugs ({entry.value})', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          loincCode: 'placeholder-code',
          name: 'Bad Data',
          valueString: '{entry.value}',
        }),
        makeObs({
          id: 'b',
          loincCode: 'good-code',
          name: 'Good Data',
          value: 5,
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Bad Data')
    expect(wrapper.text()).toContain('Good Data')
  })

  it('skips observations without any value', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Has Value', value: 5 }),
        makeObs({ id: 'b', loincCode: '999', name: 'No Value' }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('No Value')
  })

  it('flags interpretation H as text-danger (high)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Glucose', value: 200, interpretation: ['H'] }),
      ]),
    )
    expect(wrapper.find('.text-danger').exists()).toBe(true)
    expect(wrapper.find('.text-danger').classes()).not.toContain('fw-bold')
  })

  it('flags interpretation L as text-primary (low)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Hemoglobin', value: 8, interpretation: ['L'] }),
      ]),
    )
    expect(wrapper.find('.text-primary').exists()).toBe(true)
  })

  it('flags interpretation HH as critical (text-danger fw-bold)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'Potassium', value: 7, interpretation: ['HH'] }),
      ]),
    )
    const flagged = wrapper.find('.text-danger.fw-bold')
    expect(flagged.exists()).toBe(true)
  })

  it('falls back to numeric range comparison when interpretation is absent (high)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'Glucose',
          value: 200,
          refLow: 70,
          refHigh: 100,
        }),
      ]),
    )
    expect(wrapper.find('.text-danger').exists()).toBe(true)
  })

  it('falls back to numeric range comparison when interpretation is absent (low)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'Hemoglobin',
          value: 8,
          refLow: 12,
          refHigh: 16,
        }),
      ]),
    )
    expect(wrapper.find('.text-primary').exists()).toBe(true)
  })

  it('does not flag values inside the reference range', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'Glucose',
          value: 90,
          refLow: 70,
          refHigh: 100,
        }),
      ]),
    )
    expect(wrapper.find('.text-danger').exists()).toBe(false)
    expect(wrapper.find('.text-primary').exists()).toBe(false)
  })

  it('renders an SVG sparkline when an analyte has 3+ data points', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          loincCode: '6690-2',
          name: 'WBC',
          value: 6.0,
          effectiveDateTime: '2024-01-01',
        }),
        makeObs({
          id: 'b',
          loincCode: '6690-2',
          name: 'WBC',
          value: 7.0,
          effectiveDateTime: '2023-06-01',
        }),
        makeObs({
          id: 'c',
          loincCode: '6690-2',
          name: 'WBC',
          value: 5.5,
          effectiveDateTime: '2022-01-01',
        }),
      ]),
    )
    const svg = wrapper.find('svg.lab-sparkline')
    expect(svg.exists()).toBe(true)
    const polyline = svg.find('polyline')
    expect(polyline.exists()).toBe(true)
    expect(polyline.attributes('points')).not.toBe('')
  })

  it('omits the sparkline when there are fewer than 3 data points', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          loincCode: '6690-2',
          name: 'WBC',
          value: 6.0,
          effectiveDateTime: '2024-01-01',
        }),
        makeObs({
          id: 'b',
          loincCode: '6690-2',
          name: 'WBC',
          value: 7.0,
          effectiveDateTime: '2023-06-01',
        }),
      ]),
    )
    expect(wrapper.find('svg.lab-sparkline').exists()).toBe(false)
  })

  it('renders the reference range when both bounds are present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'Glucose',
          value: 90,
          unit: 'mg/dL',
          refLow: 70,
          refHigh: 100,
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Range:\s*70–100\s*mg\/dL/)
  })

  it('renders the collected date', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          name: 'WBC',
          value: 6.9,
          effectiveDateTime: '2024-08-15',
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Collected:\s*Aug\s*15,?\s*2024/)
  })

  it('sorts rows by the latest observation effectiveDateTime descending', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({
          id: 'a',
          loincCode: '111',
          name: 'Older Analyte',
          value: 1,
          effectiveDateTime: '2020-01-01',
        }),
        makeObs({
          id: 'b',
          loincCode: '222',
          name: 'Newer Analyte',
          value: 2,
          effectiveDateTime: '2024-01-01',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('Newer Analyte')
    expect(items[1]?.text()).toContain('Older Analyte')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', loincCode: '1', name: 'A', value: 1 }),
        makeObs({ id: 'b', loincCode: '2', name: 'B', value: 2 }),
        makeObs({ id: 'c', loincCode: '3', name: 'C', value: 3 }),
      ]),
    )
    expect(wrapper.text()).toContain('(3)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(LabResultsCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(LabResultsCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeObs({ id: 'a', name: 'WBC', value: 6.9, unit: '10*3/uL' }),
      ]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })
})
