import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MedicationsCard from '@/components/MedicationsCard.vue'

interface MakeMedRequestOptions {
  id?: string
  status?: 'active' | 'on-hold' | 'completed' | 'stopped' | 'cancelled' | 'draft'
  medicationText?: string
  medicationCoding?: string
  medicationReferenceDisplay?: string
  medicationReferenceOnly?: boolean
  dosageText?: string
  authoredOn?: string
  requesterDisplay?: string
}

function makeMedRequest(opts: MakeMedRequestOptions): fhir4.MedicationRequest {
  const m: fhir4.MedicationRequest = {
    resourceType: 'MedicationRequest',
    id: opts.id ?? 'mr-1',
    status: opts.status ?? 'active',
    intent: 'order',
    subject: { reference: 'Patient/p1' },
  }
  if (opts.medicationReferenceOnly === true) {
    m.medicationReference = {
      reference: 'Medication/x',
      ...(opts.medicationReferenceDisplay !== undefined
        ? { display: opts.medicationReferenceDisplay }
        : {}),
    }
  } else if (
    opts.medicationText !== undefined
    || opts.medicationCoding !== undefined
  ) {
    const cc: fhir4.CodeableConcept = {}
    if (opts.medicationText !== undefined) cc.text = opts.medicationText
    if (opts.medicationCoding !== undefined) {
      cc.coding = [
        {
          system: 'http://www.nlm.nih.gov/research/umls/rxnorm',
          code: 'X',
          display: opts.medicationCoding,
        },
      ]
    }
    m.medicationCodeableConcept = cc
  }
  if (opts.dosageText !== undefined) {
    m.dosageInstruction = [{ text: opts.dosageText }]
  }
  if (opts.authoredOn !== undefined) m.authoredOn = opts.authoredOn
  if (opts.requesterDisplay !== undefined) {
    m.requester = {
      reference: 'Practitioner/p',
      display: opts.requesterDisplay,
    }
  }
  return m
}

function makeBundle(meds: fhir4.MedicationRequest[]): fhir4.Bundle {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: meds.map((resource) => ({ resource })),
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
  const wrapper = mount(MedicationsCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<MedicationsCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/MedicationRequest with status=active', () => {
    mockFetchResolved(makeBundle([]))
    mount(MedicationsCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/MedicationRequest?patient=patient-42&status=active',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the empty copy when no active medications are on file', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('No active medications')
  })

  it('renders one row per active MedicationRequest', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationText: 'Lisinopril 10 MG' }),
        makeMedRequest({ id: 'm2', medicationText: 'Metformin 500 MG' }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Lisinopril 10 MG')
    expect(wrapper.text()).toContain('Metformin 500 MG')
  })

  it('filters out non-active statuses client-side', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Active Med',
          status: 'active',
        }),
        makeMedRequest({
          id: 'm2',
          medicationText: 'Completed Med',
          status: 'completed',
        }),
        makeMedRequest({
          id: 'm3',
          medicationText: 'On-Hold Med',
          status: 'on-hold',
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).toContain('Active Med')
    expect(wrapper.text()).not.toContain('Completed Med')
    expect(wrapper.text()).not.toContain('On-Hold Med')
  })

  it('falls back to coding[0].display when medicationCodeableConcept.text is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationCoding: 'Acetaminophen 160 MG Chewable Tablet',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Acetaminophen 160 MG Chewable Tablet')
  })

  it('falls back to medicationReference.display when no CodeableConcept is present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationReferenceOnly: true,
          medicationReferenceDisplay: 'Aspirin 81 MG',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Aspirin 81 MG')
  })

  it('renders a placeholder when the reference has no display and no CodeableConcept', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationReferenceOnly: true }),
      ]),
    )
    expect(wrapper.text()).toContain('(referenced medication)')
  })

  it('renders the dosage instruction text', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          dosageText: 'Take 1 tablet by mouth once daily',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Take 1 tablet by mouth once daily')
  })

  it('renders the requester display name', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          requesterDisplay: 'Dr. Latoyia Lindgren',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Dr. Latoyia Lindgren')
  })

  it('renders the authoredOn date', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          authoredOn: '2024-04-12',
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Started:\s*Apr\s*12,?\s*2024/)
  })

  it('renders the Active status badge', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationText: 'Lisinopril' }),
      ]),
    )
    expect(wrapper.find('.badge.bg-primary').text()).toBe('Active')
  })

  it('sorts by authoredOn descending', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'older',
          medicationText: 'Old Med',
          authoredOn: '2020-01-01T00:00:00Z',
        }),
        makeMedRequest({
          id: 'newest',
          medicationText: 'New Med',
          authoredOn: '2024-06-15T00:00:00Z',
        }),
        makeMedRequest({
          id: 'middle',
          medicationText: 'Middle Med',
          authoredOn: '2022-03-01T00:00:00Z',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('New Med')
    expect(items[1]?.text()).toContain('Middle Med')
    expect(items[2]?.text()).toContain('Old Med')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationText: 'A' }),
        makeMedRequest({ id: 'm2', medicationText: 'B' }),
        makeMedRequest({ id: 'm3', medicationText: 'C' }),
      ]),
    )
    expect(wrapper.text()).toContain('(3)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(MedicationsCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(MedicationsCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([makeMedRequest({ id: 'm1', medicationText: 'X' })]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })
})
