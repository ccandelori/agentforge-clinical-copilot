import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AllergiesCard from '@/components/AllergiesCard.vue'

// Search-result bundle factory so each test can declare a small,
// readable set of AllergyIntolerance rows. The real BFF returns a
// `Bundle` with `type: 'searchset'` and `entry[].resource` pointing at
// each AllergyIntolerance.

interface MakeAllergyOptions {
  id?: string
  substance?: string
  substanceCoding?: string
  criticality?: 'high' | 'low' | 'unable-to-assess'
  reactions?: { manifestation: string; severity?: 'mild' | 'moderate' | 'severe' }[]
  clinicalStatusCode?: string
  recordedDate?: string
}

function makeAllergy(opts: MakeAllergyOptions): fhir4.AllergyIntolerance {
  const code: fhir4.CodeableConcept = {}
  if (opts.substance !== undefined) code.text = opts.substance
  if (opts.substanceCoding !== undefined) {
    code.coding = [{ system: 'http://snomed.info/sct', code: 'X', display: opts.substanceCoding }]
  }
  const a: fhir4.AllergyIntolerance = {
    resourceType: 'AllergyIntolerance',
    id: opts.id ?? 'a1',
    code,
    patient: { reference: 'Patient/p1' },
  }
  if (opts.criticality !== undefined) a.criticality = opts.criticality
  if (opts.recordedDate !== undefined) a.recordedDate = opts.recordedDate
  if (opts.clinicalStatusCode !== undefined) {
    a.clinicalStatus = {
      coding: [
        {
          system: 'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical',
          code: opts.clinicalStatusCode,
        },
      ],
    }
  }
  if (opts.reactions !== undefined && opts.reactions.length > 0) {
    a.reaction = opts.reactions.map((r) => {
      const out: fhir4.AllergyIntoleranceReaction = {
        manifestation: [{ text: r.manifestation }],
      }
      if (r.severity !== undefined) out.severity = r.severity
      return out
    })
  }
  return a
}

function makeBundle(allergies: fhir4.AllergyIntolerance[]): fhir4.Bundle {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: allergies.map((resource) => ({ resource })),
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
  const wrapper = mount(AllergiesCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<AllergiesCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/AllergyIntolerance with the patient pid', () => {
    mockFetchResolved(makeBundle([]))
    mount(AllergiesCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/AllergyIntolerance?patient=patient-42',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the NKA badge and copy when the bundle is empty', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('NKA')
    expect(wrapper.text()).toContain('No known allergies')
  })

  it('renders one row per AllergyIntolerance entry', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substance: 'Peanuts' }),
        makeAllergy({ id: 'a2', substance: 'Penicillin' }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Peanuts')
    expect(wrapper.text()).toContain('Penicillin')
  })

  it('falls back to coding[0].display when code.text is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substanceCoding: 'Bee venom (substance)' }),
      ]),
    )
    expect(wrapper.text()).toContain('Bee venom (substance)')
  })

  it('shows reaction manifestation text when present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({
          id: 'a1',
          substance: 'Peanuts',
          reactions: [{ manifestation: 'Hives', severity: 'moderate' }],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Hives')
    expect(wrapper.text()).toContain('moderate')
  })

  it('applies the severity badge class for severe reactions', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({
          id: 'a1',
          substance: 'Penicillin',
          reactions: [{ manifestation: 'Anaphylaxis', severity: 'severe' }],
        }),
      ]),
    )
    const badge = wrapper.find('.badge.bg-danger')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('severe')
  })

  it('uses the worst severity across multiple reactions', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({
          id: 'a1',
          substance: 'Mixed',
          reactions: [
            { manifestation: 'Itching', severity: 'mild' },
            { manifestation: 'Wheezing', severity: 'severe' },
          ],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('severe')
    expect(wrapper.find('.badge.bg-danger').exists()).toBe(true)
  })

  it('shows the ⚠ HIGH indicator for high-criticality entries', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substance: 'Peanuts', criticality: 'high' }),
      ]),
    )
    expect(wrapper.text()).toContain('HIGH')
  })

  it('does not show the HIGH indicator for low-criticality entries', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substance: 'Peanuts', criticality: 'low' }),
      ]),
    )
    expect(wrapper.text()).not.toContain('HIGH')
  })

  it('sorts high-criticality entries before low/unknown', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substance: 'Mild Allergen', criticality: 'low' }),
        makeAllergy({ id: 'a2', substance: 'Severe Allergen', criticality: 'high' }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('Severe Allergen')
    expect(items[1]?.text()).toContain('Mild Allergen')
  })

  it('within the same criticality, sorts by recordedDate descending', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({
          id: 'older',
          substance: 'Old Allergen',
          criticality: 'high',
          recordedDate: '2010-01-01T00:00:00Z',
        }),
        makeAllergy({
          id: 'newer',
          substance: 'New Allergen',
          criticality: 'high',
          recordedDate: '2024-06-15T00:00:00Z',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('New Allergen')
    expect(items[1]?.text()).toContain('Old Allergen')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({ id: 'a1', substance: 'A' }),
        makeAllergy({ id: 'a2', substance: 'B' }),
        makeAllergy({ id: 'a3', substance: 'C' }),
      ]),
    )
    expect(wrapper.text()).toContain('(3)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(AllergiesCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(AllergiesCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([makeAllergy({ id: 'a1', substance: 'Peanuts' })]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })

  it('renders the clinical status when present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeAllergy({
          id: 'a1',
          substance: 'Peanuts',
          clinicalStatusCode: 'active',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Active')
  })
})
