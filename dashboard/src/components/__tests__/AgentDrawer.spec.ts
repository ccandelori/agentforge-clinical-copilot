import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import AgentDrawer from '@/components/AgentDrawer.vue'
import { useAgentDrawer } from '@/stores/agentDrawer'

function mockAgentReply(reply: string): ReturnType<typeof vi.fn<typeof fetch>> {
  const spy = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify({ reply }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  )
  globalThis.fetch = spy as unknown as typeof fetch
  return spy
}

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

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('mounts without throwing', () => {
    const wrapper = mountDrawer()
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the right-edge toggle button when closed', () => {
    const wrapper = mountDrawer()
    expect(wrapper.find('[data-test="agent-drawer-toggle"]').exists()).toBe(true)
  })

  it('does not render the drawer body when closed', () => {
    // Regression: Bootstrap .d-flex !important overrides Vue's v-show
    // inline display:none, so the aside has to be removed from the DOM
    // entirely (v-if), not merely toggled with v-show.
    const wrapper = mountDrawer()
    expect(wrapper.find('[data-test="agent-drawer"]').exists()).toBe(false)
  })

  it('clicking the in-drawer close button collapses the drawer back to the toggle', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="agent-drawer"]').exists()).toBe(true)

    await wrapper.find('[data-test="agent-drawer-close"]').trigger('click')
    expect(store.open).toBe(false)
    expect(wrapper.find('[data-test="agent-drawer"]').exists()).toBe(false)
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
    mockAgentReply('ok')
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('42')
    store.setMode('chart')
    await wrapper.vm.$nextTick()

    const input = wrapper.find<HTMLInputElement>('[data-test="agent-input"]')
    await input.setValue('what should I ask?')
    await wrapper.find('[data-test="agent-send"]').trigger('click')

    // The user turn lands synchronously; the assistant reply arrives
    // after the awaited fetch resolves (covered in a separate test).
    expect(store.currentMessages[0]?.text).toBe('what should I ask?')
    expect(store.currentMessages[0]?.role).toBe('user')
    expect(input.element.value).toBe('')
  })

  it('does not send empty / whitespace-only input', async () => {
    mockAgentReply('ok')
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('42')
    store.setMode('chart')
    await wrapper.vm.$nextTick()

    const input = wrapper.find<HTMLInputElement>('[data-test="agent-input"]')
    await input.setValue('   ')
    await wrapper.find('[data-test="agent-send"]').trigger('click')

    expect(store.currentMessages).toHaveLength(0)
  })

  it('appends the assistant reply to the store on a successful agent turn', async () => {
    const fetchSpy = mockAgentReply('the patient has 2 active allergies.')
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('42')
    store.setMode('chart')
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="agent-input"]').setValue('any allergies?')
    await wrapper.find('[data-test="agent-send"]').trigger('click')
    await flushPromises()

    expect(fetchSpy).toHaveBeenCalledOnce()
    const init = fetchSpy.mock.calls[0]?.[1]
    expect(init?.method).toBe('POST')
    const body = JSON.parse(init?.body as string)
    expect(body.message).toBe('any allergies?')
    expect(body.patient_id).toBe(42)
    expect(body.session_id).toBe('chart:42')

    expect(store.currentMessages).toHaveLength(2)
    expect(store.currentMessages[1]?.text).toBe('the patient has 2 active allergies.')
    expect(store.currentMessages[1]?.role).toBe('assistant')
  })

  it('appends an error assistant turn when the agent call fails', async () => {
    globalThis.fetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('nope', { status: 502 })) as unknown as typeof fetch
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    store.setActivePatient('42')
    store.setMode('chart')
    await wrapper.vm.$nextTick()

    await wrapper.find('[data-test="agent-input"]').setValue('q')
    await wrapper.find('[data-test="agent-send"]').trigger('click')
    await flushPromises()

    expect(store.currentMessages).toHaveLength(2)
    expect(store.currentMessages[1]?.role).toBe('assistant')
    expect(store.currentMessages[1]?.text.toLowerCase()).toContain('error')
  })

  it('disables the input outside Chart mode (Research/Intake aren\'t wired yet)', async () => {
    const wrapper = mountDrawer()
    const store = useAgentDrawer()
    store.openDrawer()
    // No active patient — default mode is research, Chart unavailable.
    await wrapper.vm.$nextTick()

    expect(
      wrapper.find('[data-test="agent-input"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.find('[data-test="agent-send"]').attributes('disabled'),
    ).toBeDefined()
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
