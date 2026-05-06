import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProblemListCard from '@/components/ProblemListCard.vue'

interface MakeConditionOptions {
  id?: string
  problem?: string
  problemCoding?: string
  clinicalStatusCode?: string
  verificationStatusCode?: string
  category?: string
  onsetDateTime?: string
  onsetPeriodStart?: string
  onsetString?: string
  recordedDate?: string
}

function makeCondition(opts: MakeConditionOptions): fhir4.Condition {
  const code: fhir4.CodeableConcept = {}
  if (opts.problem !== undefined) code.text = opts.problem
  if (opts.problemCoding !== undefined) {
    code.coding = [
      { system: 'http://snomed.info/sct', code: 'X', display: opts.problemCoding },
    ]
  }
  const c: fhir4.Condition = {
    resourceType: 'Condition',
    id: opts.id ?? 'cond-1',
    code,
    subject: { reference: 'Patient/p1' },
  }
  // Default to problem-list-item; tests opt out by passing a different
  // category to verify the client-side filter.
  const category = opts.category ?? 'problem-list-item'
  c.category = [
    {
      coding: [
        {
          system: 'http://terminology.hl7.org/CodeSystem/condition-category',
          code: category,
        },
      ],
    },
  ]
  if (opts.clinicalStatusCode !== undefined) {
    c.clinicalStatus = {
      coding: [
        {
          system: 'http://terminology.hl7.org/CodeSystem/condition-clinical',
          code: opts.clinicalStatusCode,
        },
      ],
    }
  }
  if (opts.verificationStatusCode !== undefined) {
    c.verificationStatus = {
      coding: [
        {
          system: 'http://terminology.hl7.org/CodeSystem/condition-ver-status',
          code: opts.verificationStatusCode,
        },
      ],
    }
  }
  if (opts.onsetDateTime !== undefined) c.onsetDateTime = opts.onsetDateTime
  if (opts.onsetPeriodStart !== undefined) {
    c.onsetPeriod = { start: opts.onsetPeriodStart }
  }
  if (opts.onsetString !== undefined) c.onsetString = opts.onsetString
  if (opts.recordedDate !== undefined) c.recordedDate = opts.recordedDate
  return c
}

function makeBundle(conditions: fhir4.Condition[]): fhir4.Bundle {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: conditions.map((resource) => ({ resource })),
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
  const wrapper = mount(ProblemListCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<ProblemListCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/Condition with category=problem-list-item', () => {
    mockFetchResolved(makeBundle([]))
    mount(ProblemListCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/Condition?patient=patient-42&category=problem-list-item',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the empty copy when the bundle has no conditions', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('No problems on file')
  })

  it('renders one row per problem-list-item condition', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({ id: 'c1', problem: 'Hypertension' }),
        makeCondition({ id: 'c2', problem: 'Type 2 diabetes' }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Hypertension')
    expect(wrapper.text()).toContain('Type 2 diabetes')
  })

  it('filters out non-problem-list-item conditions client-side', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Encounter Diagnosis Item',
          category: 'encounter-diagnosis',
        }),
        makeCondition({
          id: 'c2',
          problem: 'Problem List Item',
          category: 'problem-list-item',
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).toContain('Problem List Item')
    expect(wrapper.text()).not.toContain('Encounter Diagnosis Item')
  })

  it('falls back to coding[0].display when code.text is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({ id: 'c1', problemCoding: 'Asthma (disorder)' }),
      ]),
    )
    expect(wrapper.text()).toContain('Asthma (disorder)')
  })

  it('renders the clinicalStatus as a capitalized badge', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Hypertension',
          clinicalStatusCode: 'active',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-primary').text()).toBe('Active')
  })

  it('uses bg-success for resolved status', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Past Issue',
          clinicalStatusCode: 'resolved',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-success').text()).toBe('Resolved')
  })

  it('uses bg-secondary for inactive status', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Old Issue',
          clinicalStatusCode: 'inactive',
        }),
      ]),
    )
    expect(wrapper.find('.badge.bg-secondary').text()).toBe('Inactive')
  })

  it('shows verification status only when not confirmed', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Confirmed item',
          verificationStatusCode: 'confirmed',
        }),
        makeCondition({
          id: 'c2',
          problem: 'Provisional item',
          verificationStatusCode: 'provisional',
        }),
      ]),
    )
    // 'confirmed' is the default state and is not annotated.
    expect(wrapper.text()).not.toContain('(confirmed)')
    expect(wrapper.text()).toContain('(provisional)')
  })

  it('sorts active conditions before resolved', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Past Diabetes',
          clinicalStatusCode: 'resolved',
        }),
        makeCondition({
          id: 'c2',
          problem: 'Active Hypertension',
          clinicalStatusCode: 'active',
        }),
        makeCondition({
          id: 'c3',
          problem: 'Inactive Asthma',
          clinicalStatusCode: 'inactive',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('Active Hypertension')
    expect(items[1]?.text()).toContain('Inactive Asthma')
    expect(items[2]?.text()).toContain('Past Diabetes')
  })

  it('within the same status group, sorts by recordedDate descending', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'older',
          problem: 'Old Problem',
          clinicalStatusCode: 'active',
          recordedDate: '2010-01-01T00:00:00Z',
        }),
        makeCondition({
          id: 'newer',
          problem: 'New Problem',
          clinicalStatusCode: 'active',
          recordedDate: '2024-06-15T00:00:00Z',
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    expect(items[0]?.text()).toContain('New Problem')
    expect(items[1]?.text()).toContain('Old Problem')
  })

  it('renders onsetDateTime when present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Hypertension',
          onsetDateTime: '2020-03-15',
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Onset:\s*Mar\s*15,?\s*2020/)
  })

  it('falls back to onsetPeriod.start when onsetDateTime is absent', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Period-onset Issue',
          onsetPeriodStart: '2018-07-04',
        }),
      ]),
    )
    expect(wrapper.text()).toMatch(/Onset:\s*Jul\s*4,?\s*2018/)
  })

  it('falls back to onsetString when neither dateTime nor period is set', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({
          id: 'c1',
          problem: 'Vague-onset Issue',
          onsetString: 'around childhood',
        }),
      ]),
    )
    expect(wrapper.text()).toContain('around childhood')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCondition({ id: 'c1', problem: 'A' }),
        makeCondition({ id: 'c2', problem: 'B' }),
      ]),
    )
    expect(wrapper.text()).toContain('(2)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(ProblemListCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(ProblemListCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([makeCondition({ id: 'c1', problem: 'X' })]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })
})
