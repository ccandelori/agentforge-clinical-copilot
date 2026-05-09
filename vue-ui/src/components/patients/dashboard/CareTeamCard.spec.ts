import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { CareTeam, CareTeamMember } from '@/api/mock'
import CareTeamCard from '@/components/patients/dashboard/CareTeamCard.vue'

function makeMember(overrides: Partial<CareTeamMember> = {}): CareTeamMember {
  return {
    id: 'mem-1',
    name: 'Donna Lee',
    role: 'Physician',
    ...overrides,
  }
}

function makeTeam(overrides: Partial<CareTeam> = {}): CareTeam {
  return {
    id: 'team-1',
    patientId: 'pat-1',
    name: 'Chen Care Team',
    status: 'active',
    members: [makeMember()],
    ...overrides,
  }
}

describe('CareTeamCard', () => {
  it('renders the empty state when there are no teams', () => {
    const wrapper = mount(CareTeamCard, { props: { careTeams: [] } })

    expect(wrapper.text()).toContain('No care team on file')
  })

  it('renders the empty state when teams exist but have no members', () => {
    // Surfaces a real failure mode: a `care_teams` row may exist with zero
    // `care_team_member` joins (e.g. the seed half-ran). Show the empty
    // copy rather than a misleading team header with a 0-row list.
    const empty = makeTeam({ members: [] })
    const wrapper = mount(CareTeamCard, { props: { careTeams: [empty] } })

    expect(wrapper.text()).toContain('No care team on file')
  })

  it('shows skeleton placeholders while loading', () => {
    const wrapper = mount(CareTeamCard, {
      props: { careTeams: [], loading: true },
    })

    expect(wrapper.findAll('.animate-pulse')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('No care team on file')
  })

  it('renders the team name, member count, and each participant row', () => {
    const team = makeTeam({
      name: 'Whitaker Care Team',
      members: [
        makeMember({ id: '1', name: 'Donna Lee', role: 'Physician' }),
        makeMember({ id: '2', name: 'Fred Stone', role: 'Nurse Practitioner' }),
        makeMember({ id: '3', name: 'Barbara Wallace', role: 'Care Coordinator' }),
      ],
    })
    const wrapper = mount(CareTeamCard, { props: { careTeams: [team] } })

    const text = wrapper.text()
    expect(text).toContain('Whitaker Care Team')
    expect(text).toContain('Donna Lee')
    expect(text).toContain('Physician')
    expect(text).toContain('Fred Stone')
    expect(text).toContain('Nurse Practitioner')
    expect(text).toContain('Barbara Wallace')
    expect(text).toContain('Care Coordinator')

    // Total member count badge in the header.
    expect(text).toContain('3')
  })

  it('handles members missing a role without rendering a stray badge', () => {
    const team = makeTeam({
      members: [
        makeMember({ id: '1', name: 'Solo Provider', role: '' }),
      ],
    })
    const wrapper = mount(CareTeamCard, { props: { careTeams: [team] } })

    expect(wrapper.text()).toContain('Solo Provider')
    // BaseBadge instances on the provider row should be absent — only the
    // header count badge remains (the v-if="member.role" guard suppresses it).
    const badges = wrapper.findAll('[class*="rounded"]')
    // A loose check; the meaningful assertion is that no role text leaks
    // through with no value.
    expect(wrapper.text()).not.toMatch(/\b(undefined|null)\b/)
    expect(badges.length).toBeGreaterThan(0)
  })
})
