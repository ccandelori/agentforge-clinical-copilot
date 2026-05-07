import { beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import PatientContextConflictOverlay from '@/components/PatientContextConflictOverlay.vue'
import { useAgentDrawer } from '@/stores/agentDrawer'

// The overlay is the hard-interrupt UI mandated by the panel-design
// grilling 2026-05-06: when the active patient changes mid-Chart-
// conversation, render an overlay (NOT a banner) and require an
// explicit choice before the chat input re-enables.

function mountOverlay(): VueWrapper {
  return mount(PatientContextConflictOverlay, { attachTo: document.body })
}

describe('<PatientContextConflictOverlay>', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders nothing when no patient-change is pending', () => {
    const wrapper = mountOverlay()
    expect(wrapper.find('[data-test="patient-conflict-overlay"]').exists()).toBe(false)
  })

  it('renders the overlay with both patient ids when pending', async () => {
    const wrapper = mountOverlay()
    const store = useAgentDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('q')
    store.setActivePatient('p2')
    await wrapper.vm.$nextTick()

    const overlay = wrapper.find('[data-test="patient-conflict-overlay"]')
    expect(overlay.exists()).toBe(true)
    expect(overlay.text()).toContain('p1')
    expect(overlay.text()).toContain('p2')
  })

  it('Switch button resolves the conflict with "switch"', async () => {
    const wrapper = mountOverlay()
    const store = useAgentDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('q')
    store.setActivePatient('p2')
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="patient-conflict-switch"]').trigger('click')
    expect(store.activePatient).toBe('p2')
    expect(store.pendingPatientChange).toBeNull()
  })

  it('Stay button resolves the conflict with "stay"', async () => {
    const wrapper = mountOverlay()
    const store = useAgentDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('q')
    store.setActivePatient('p2')
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="patient-conflict-stay"]').trigger('click')
    expect(store.activePatient).toBe('p1')
    expect(store.pendingPatientChange).toBeNull()
  })

  it('does not render the Fresh button when the target has no stale conversation', async () => {
    const wrapper = mountOverlay()
    const store = useAgentDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('q')
    store.setActivePatient('p2') // p2 has no prior chart history
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="patient-conflict-fresh"]').exists()).toBe(false)
  })

  it('renders Fresh button when the target has stale chart history; click discards it then switches', async () => {
    const wrapper = mountOverlay()
    const store = useAgentDrawer()

    // Build chart history on p1, then on p2.
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('p1 q')

    store.setActivePatient('p2')
    store.resolvePatientChange('switch')
    store.addUserTurn('p2 stale')

    // Hop back to p1, accumulate progress, then attempt to switch back to p2
    // (which now has stale chart history).
    store.setActivePatient('p1')
    store.resolvePatientChange('switch')
    store.addUserTurn('p1 followup')

    store.setActivePatient('p2')
    await wrapper.vm.$nextTick()

    const fresh = wrapper.find('[data-test="patient-conflict-fresh"]')
    expect(fresh.exists()).toBe(true)

    await fresh.trigger('click')
    expect(store.activePatient).toBe('p2')
    expect(store.pendingPatientChange).toBeNull()
    expect(store.currentMessages).toEqual([])
  })
})
