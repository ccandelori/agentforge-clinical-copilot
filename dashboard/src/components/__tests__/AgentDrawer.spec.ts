import { beforeEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import AgentDrawer from '@/components/AgentDrawer.vue'
import { useAgentDrawer } from '@/stores/agentDrawer'

// Slice 2 covers the drawer shell only:
//   * right-edge toggle, slide-out body
//   * three mode tabs (Chart disabled until canChart === true)
//   * chat list rendered from store.currentMessages
//   * input + send append a user turn into the active scope
//   * input/send freeze while a patient-context conflict is pending
//
// The PatientContextConflictOverlay component itself is tested in its
// own spec (Slice 3); here we only verify that input is disabled while
// the overlay condition holds.

function mountDrawer(): VueWrapper {
  return mount(AgentDrawer, {
    global: {
      stubs: {
        // overlay is a sibling component; stub it so the drawer spec is
        // independent of overlay markup. Slice 3 covers the overlay.
        PatientContextConflictOverlay: true,
      },
    },
    attachTo: document.body,
  })
}

describe('<AgentDrawer>', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('mounts without throwing', () => {
    const wrapper = mountDrawer()
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the right-edge toggle button when closed', () => {
    const wrapper = mountDrawer()
    expect(wrapper.find('[data-test="agent-drawer-toggle"]').exists()).toBe(true)
  })

  it('clicking the toggle opens the drawer', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    expect(store.open).toBe(false)
    await wrapper.find('[data-test="agent-drawer-toggle"]').trigger('click')
    expect(store.open).toBe(true)
  })

  it('renders three mode tabs in the header', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="agent-tab-chart"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="agent-tab-intake"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="agent-tab-research"]').exists()).toBe(true)
  })

  it('disables the Chart tab when no patient is active', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    await wrapper.vm.$nextTick()

    const chartTab = wrapper.find('[data-test="agent-tab-chart"]')
    expect(chartTab.attributes('disabled')).toBeDefined()
  })

  it('enables the Chart tab once an active patient is set', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('p1')
    await wrapper.vm.$nextTick()

    const chartTab = wrapper.find('[data-test="agent-tab-chart"]')
    expect(chartTab.attributes('disabled')).toBeUndefined()
  })

  it('clicking an enabled mode tab updates the store mode', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('p1')
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="agent-tab-chart"]').trigger('click')
    expect(store.mode).toBe('chart')

    await wrapper.find('[data-test="agent-tab-research"]').trigger('click')
    expect(store.mode).toBe('research')
  })

  it('marks the active mode tab with aria-selected="true"', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-test="agent-tab-chart"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('[data-test="agent-tab-research"]').attributes('aria-selected')).toBe('false')
  })

  it('renders messages from the current scope', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.addUserTurn('hello research')
    store.addAssistantTurn('hi human')
    await wrapper.vm.$nextTick()

    const list = wrapper.find('[data-test="agent-message-list"]')
    expect(list.text()).toContain('hello research')
    expect(list.text()).toContain('hi human')
  })

  it('typing into the input and clicking send appends a user turn and clears the input', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    await wrapper.vm.$nextTick()

    const input = wrapper.find<HTMLInputElement>('[data-test="agent-input"]')
    await input.setValue('what should I ask?')
    await wrapper.find('[data-test="agent-send"]').trigger('click')

    expect(store.currentMessages).toHaveLength(1)
    expect(store.currentMessages[0]?.text).toBe('what should I ask?')
    expect(store.currentMessages[0]?.role).toBe('user')
    expect(input.element.value).toBe('')
  })

  it('does not send empty / whitespace-only input', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    await wrapper.vm.$nextTick()

    const input = wrapper.find<HTMLInputElement>('[data-test="agent-input"]')
    await input.setValue('   ')
    await wrapper.find('[data-test="agent-send"]').trigger('click')

    expect(store.currentMessages).toHaveLength(0)
  })

  it('disables the input and send button while a pendingPatientChange is staged', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('p1')
    store.setMode('chart')
    store.addUserTurn('progress')
    store.setActivePatient('p2') // stages pending change
    await wrapper.vm.$nextTick()

    expect(store.pendingPatientChange).not.toBeNull()
    const input = wrapper.find('[data-test="agent-input"]')
    const send = wrapper.find('[data-test="agent-send"]')
    expect(input.attributes('disabled')).toBeDefined()
    expect(send.attributes('disabled')).toBeDefined()
  })
})
