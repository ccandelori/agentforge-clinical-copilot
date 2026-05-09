import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { Medication } from '@/api/mock'
import PrescriptionsCard from '@/components/patients/dashboard/PrescriptionsCard.vue'

function makeMed(overrides: Partial<Medication> = {}): Medication {
  return {
    id: 'med-1',
    patientId: 'pat-1',
    name: 'Lisinopril',
    dose: '10 mg',
    route: 'oral',
    frequency: 'once daily',
    status: 'completed',
    prescribedDate: '2025-01-10',
    prescriber: 'Dr Foo',
    ...overrides,
  }
}

describe('PrescriptionsCard', () => {
  it('renders the empty state when no medications match the past filter', () => {
    // Active meds belong to MedicationsCard, so they should NOT show here
    // even when the underlying list is non-empty.
    const wrapper = mount(PrescriptionsCard, {
      props: {
        medications: [
          makeMed({ id: 'a', status: 'active', name: 'Atorvastatin' }),
        ],
      },
    })

    expect(wrapper.text()).toContain('No past prescriptions')
    expect(wrapper.text()).not.toContain('Atorvastatin')
  })

  it('renders the empty state when the list is genuinely empty', () => {
    const wrapper = mount(PrescriptionsCard, { props: { medications: [] } })

    expect(wrapper.text()).toContain('No past prescriptions')
  })

  it('shows skeleton placeholders while loading', () => {
    const wrapper = mount(PrescriptionsCard, {
      props: { medications: [], loading: true },
    })

    // 3 skeleton rows, no empty-state copy.
    expect(wrapper.findAll('.animate-pulse')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('No past prescriptions')
  })

  it('lists completed and stopped medications, hiding actives', () => {
    const meds: readonly Medication[] = [
      makeMed({ id: '1', name: 'Active Med', status: 'active' }),
      makeMed({ id: '2', name: 'Completed Med', status: 'completed' }),
      makeMed({ id: '3', name: 'Stopped Med', status: 'stopped' }),
    ]
    const wrapper = mount(PrescriptionsCard, { props: { medications: meds } })

    const text = wrapper.text()
    expect(text).toContain('Completed Med')
    expect(text).toContain('Stopped Med')
    expect(text).not.toContain('Active Med')

    // Header count badge should reflect the past-only count.
    expect(text).toContain('2')
  })

  it('orders stopped scripts before completed ones, then by date desc', () => {
    const meds: readonly Medication[] = [
      makeMed({
        id: 'old-completed',
        name: 'Old Completed',
        status: 'completed',
        prescribedDate: '2023-06-01',
      }),
      makeMed({
        id: 'recent-completed',
        name: 'Recent Completed',
        status: 'completed',
        prescribedDate: '2025-04-15',
      }),
      makeMed({
        id: 'stopped',
        name: 'Stopped Med',
        status: 'stopped',
        prescribedDate: '2024-08-20',
      }),
    ]
    const wrapper = mount(PrescriptionsCard, { props: { medications: meds } })

    const items = wrapper.findAll('li')
    expect(items).toHaveLength(3)
    expect(items[0]!.text()).toContain('Stopped Med')
    expect(items[1]!.text()).toContain('Recent Completed')
    expect(items[2]!.text()).toContain('Old Completed')
  })
})
