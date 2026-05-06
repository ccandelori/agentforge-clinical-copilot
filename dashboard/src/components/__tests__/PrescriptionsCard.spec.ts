import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrescriptionsCard from '@/components/PrescriptionsCard.vue'

interface MakeMedRequestOptions {
  id?: string
  status?:
    | 'active'
    | 'on-hold'
    | 'completed'
    | 'stopped'
    | 'cancelled'
    | 'draft'
    | 'entered-in-error'
    | 'unknown'
  medicationText?: string
  medicationCoding?: string
  medicationReferenceDisplay?: string
  medicationReferenceOnly?: boolean
  dosageText?: string
  authoredOn?: string
  requesterDisplay?: string
  numberOfRepeatsAllowed?: number
}

function makeMedRequest(opts: MakeMedRequestOptions): fhir4.MedicationRequest {
  const m: fhir4.MedicationRequest = {
    resourceType: 'MedicationRequest',
    id: opts.id ?? 'mr-1',
    status: opts.status ?? 'completed',
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
  if (opts.numberOfRepeatsAllowed !== undefined) {
    m.dispenseRequest = { numberOfRepeatsAllowed: opts.numberOfRepeatsAllowed }
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
  const wrapper = mount(PrescriptionsCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<PrescriptionsCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/MedicationRequest without a status filter', () => {
    mockFetchResolved(makeBundle([]))
    mount(PrescriptionsCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/MedicationRequest?patient=patient-42',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the empty copy when no historical prescriptions exist', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('No prescription history')
  })

  it('renders one row per non-active MedicationRequest', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          status: 'completed',
        }),
        makeMedRequest({
          id: 'm2',
          medicationText: 'Metformin',
          status: 'stopped',
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Lisinopril')
    expect(wrapper.text()).toContain('Metformin')
  })

  it('filters out active prescriptions client-side', async () => {
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
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).toContain('Completed Med')
    expect(wrapper.text()).not.toContain('Active Med')
  })

  it('falls back to coding[0].display when medicationCodeableConcept.text is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationCoding: 'Acetaminophen 160 MG Chewable Tablet',
          status: 'completed',
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
          status: 'completed',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Aspirin 81 MG')
  })

  it('uses bg-success for completed status', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'X',
          status: 'completed',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-success').text()).toBe('Completed')
  })

  it('uses bg-warning for stopped status', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'X',
          status: 'stopped',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-warning').text()).toBe('Stopped')
  })

  it('uses bg-info for on-hold status', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'X',
          status: 'on-hold',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-info').text()).toBe('On-hold')
  })

  it('renders the authoredOn date prefixed with "Prescribed:"', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          status: 'completed',
          authoredOn: '2022-08-01',
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Prescribed:\s*Aug\s*1,?\s*2022/)
  })

  it('renders the requester display prefixed with "By:"', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          status: 'completed',
          requesterDisplay: 'Dr. Latoyia Lindgren',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('By: Dr. Latoyia Lindgren')
  })

  it('renders the dispenseRequest.numberOfRepeatsAllowed as Refills', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          status: 'completed',
          numberOfRepeatsAllowed: 3,
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Refills: 3')
  })

  it('omits the Refills line when numberOfRepeatsAllowed is absent', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'm1',
          medicationText: 'Lisinopril',
          status: 'completed',
        }),
      ]),
    )
    expect(wrapper.text()).not.toContain('Refills:')
  })

  it('sorts by authoredOn descending', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({
          id: 'older',
          medicationText: 'Old Rx',
          status: 'completed',
          authoredOn: '2018-01-01T00:00:00Z',
        }),
        makeMedRequest({
          id: 'newer',
          medicationText: 'New Rx',
          status: 'completed',
          authoredOn: '2024-06-15T00:00:00Z',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('New Rx')
    expect(items[1]?.text()).toContain('Old Rx')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationText: 'A', status: 'completed' }),
        makeMedRequest({ id: 'm2', medicationText: 'B', status: 'stopped' }),
      ]),
    )
    expect(wrapper.text()).toContain('(2)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(PrescriptionsCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(PrescriptionsCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeMedRequest({ id: 'm1', medicationText: 'X', status: 'completed' }),
      ]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })
})
