import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CareTeamCard from '@/components/CareTeamCard.vue'

interface MakeParticipantOptions {
  memberDisplay?: string
  memberReference?: string
  roleText?: string
  roleCodingDisplay?: string
  roleCodingCode?: string
}

interface MakeCareTeamOptions {
  id?: string
  name?: string
  status?:
    | 'proposed'
    | 'active'
    | 'suspended'
    | 'inactive'
    | 'entered-in-error'
  participants?: MakeParticipantOptions[]
}

function makeParticipant(
  opts: MakeParticipantOptions,
): fhir4.CareTeamParticipant {
  const p: fhir4.CareTeamParticipant = {}
  if (opts.memberDisplay !== undefined || opts.memberReference !== undefined) {
    p.member = {
      ...(opts.memberReference !== undefined
        ? { reference: opts.memberReference }
        : {}),
      ...(opts.memberDisplay !== undefined
        ? { display: opts.memberDisplay }
        : {}),
    }
  }
  if (
    opts.roleText !== undefined
    || opts.roleCodingDisplay !== undefined
    || opts.roleCodingCode !== undefined
  ) {
    const role: fhir4.CodeableConcept = {}
    if (opts.roleText !== undefined) role.text = opts.roleText
    if (
      opts.roleCodingDisplay !== undefined
      || opts.roleCodingCode !== undefined
    ) {
      const coding: fhir4.Coding = { system: 'http://snomed.info/sct' }
      if (opts.roleCodingDisplay !== undefined) {
        coding.display = opts.roleCodingDisplay
      }
      if (opts.roleCodingCode !== undefined) {
        coding.code = opts.roleCodingCode
      }
      role.coding = [coding]
    }
    p.role = [role]
  }
  return p
}

function makeCareTeam(opts: MakeCareTeamOptions): fhir4.CareTeam {
  const t: fhir4.CareTeam = {
    resourceType: 'CareTeam',
    id: opts.id ?? 'team-1',
  }
  if (opts.name !== undefined) t.name = opts.name
  if (opts.status !== undefined) t.status = opts.status
  if (opts.participants !== undefined) {
    t.participant = opts.participants.map(makeParticipant)
  }
  return t
}

function makeBundle(teams: fhir4.CareTeam[]): fhir4.Bundle {
  return {
    resourceType: 'Bundle',
    type: 'searchset',
    entry: teams.map((resource) => ({ resource })),
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
  const wrapper = mount(CareTeamCard, { props: { pid: 'p1' } })
  await flushPromises()
  return wrapper
}

describe('<CareTeamCard>', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('queries /api/fhir/CareTeam with the patient pid', () => {
    mockFetchResolved(makeBundle([]))
    mount(CareTeamCard, { props: { pid: 'patient-42' } })
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/fhir/CareTeam?patient=patient-42',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('shows the empty copy when no care team is assigned', async () => {
    const wrapper = await mountReady(makeBundle([]))
    expect(wrapper.text()).toContain('No care team assigned')
  })

  it('renders one row per non-patient participant', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            {
              memberDisplay: 'Dr. Mana Boehm',
              roleText: 'Healthcare professional (occupation)',
            },
            {
              memberDisplay: 'Nurse Jamie',
              roleText: 'Nursing staff',
            },
          ],
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Dr. Mana Boehm')
    expect(wrapper.text()).toContain('Nurse Jamie')
  })

  it('flattens participants across multiple CareTeam resources', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [{ memberDisplay: 'A', roleText: 'Role 1' }],
        }),
        makeCareTeam({
          id: 't2',
          participants: [{ memberDisplay: 'B', roleText: 'Role 2' }],
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('A')
    expect(wrapper.text()).toContain('B')
  })

  it('skips the patient participant (role.text === "Patient")', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            { memberDisplay: 'Andrea Schumm', roleText: 'Patient' },
            { memberDisplay: 'Dr. Boehm', roleText: 'Cardiologist' },
          ],
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Andrea Schumm')
    expect(wrapper.text()).toContain('Dr. Boehm')
  })

  it('skips the patient participant (SNOMED code 116154003)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            {
              memberDisplay: 'Andrea Schumm',
              roleCodingCode: '116154003',
              roleCodingDisplay: 'Patient',
            },
            { memberDisplay: 'Dr. Boehm', roleText: 'Cardiologist' },
          ],
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Andrea Schumm')
  })

  it('skips the patient participant (member.reference starts with Patient/)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            {
              memberDisplay: 'Andrea Schumm',
              memberReference: 'Patient/abc-123',
            },
            { memberDisplay: 'Dr. Boehm', roleText: 'Cardiologist' },
          ],
        }),
      ]),
    )
    expect(wrapper.findAll('li')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('Andrea Schumm')
  })

  it('falls back to coding[0].display when role.text is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            {
              memberDisplay: 'Dr. X',
              roleCodingDisplay: 'General Practitioner',
            },
          ],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('General Practitioner')
  })

  it('falls back to (unspecified role) when no role information is present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [{ memberDisplay: 'Dr. X' }],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('(unspecified role)')
  })

  it('falls back to (unknown member) when member.display is missing', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [{ roleText: 'Cardiologist' }],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('(unknown member)')
  })

  it('renders the team name when present', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          name: 'Diabetes Care Team',
          participants: [
            { memberDisplay: 'Dr. X', roleText: 'Endocrinologist' },
          ],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Diabetes Care Team')
  })

  it('sorts participants by role then by name', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            { memberDisplay: 'Dr. Zebra', roleText: 'Cardiologist' },
            { memberDisplay: 'Dr. Apple', roleText: 'Cardiologist' },
            { memberDisplay: 'Dr. Banana', roleText: 'Anesthesiologist' },
          ],
        }),
      ]),
    )
    const items = wrapper.findAll('li')
    // Anesthesiologist sorts before Cardiologist
    expect(items[0]?.text()).toContain('Dr. Banana')
    // Within Cardiologist: Apple before Zebra
    expect(items[1]?.text()).toContain('Dr. Apple')
    expect(items[2]?.text()).toContain('Dr. Zebra')
  })

  it('shows the count in the card header when ready', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [
            { memberDisplay: 'A', roleText: 'r1' },
            { memberDisplay: 'B', roleText: 'r2' },
            { memberDisplay: 'C', roleText: 'r3' },
          ],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('(3)')
  })

  it('renders the loading skeleton during fetch', () => {
    mockFetchResolved(makeBundle([]))
    const wrapper = mount(CareTeamCard, { props: { pid: 'p1' } })
    expect(wrapper.find('.placeholder-glow').exists()).toBe(true)
  })

  it('shows an error state when the fetch rejects', async () => {
    mockFetchRejected(new Error('boom'))
    const wrapper = mount(CareTeamCard, { props: { pid: 'p1' } })
    await flushPromises()
    expect(wrapper.text()).toContain('boom')
  })

  it('renders the chevron toggle (collapsible) and is expanded by default', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          participants: [{ memberDisplay: 'Dr. X', roleText: 'r' }],
        }),
      ]),
    )
    const btn = wrapper.find('button[aria-expanded]')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('aria-expanded')).toBe('true')
  })

  it('includes inactive CareTeams (no status filter)', async () => {
    const wrapper = await mountReady(
      makeBundle([
        makeCareTeam({
          id: 't1',
          status: 'inactive',
          participants: [{ memberDisplay: 'Dr. Past', roleText: 'r' }],
        }),
      ]),
    )
    expect(wrapper.text()).toContain('Dr. Past')
  })
})
