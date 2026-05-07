import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import {
  createMemoryHistory,
  createRouter,
  type Router,
  type RouteRecordRaw,
} from 'vue-router'
import { defineComponent, h } from 'vue'

import App from '@/App.vue'
import { useAgentDrawer } from '@/stores/agentDrawer'

vi.mock('@/services/navigation', () => ({
  navigateTo: vi.fn<(url: string) => void>(),
}))

// Stub the auth-store hydrate path so the router's beforeEach guard
// resolves immediately. We're not testing auth here.
function mockWhoamiUnauthenticated(): void {
  globalThis.fetch = vi
    .fn<typeof fetch>()
    .mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ authenticated: false }),
    } as unknown as Response) as unknown as typeof fetch
}

const StubLogin = defineComponent({
  name: 'StubLogin',
  setup: () => () => h('div', { 'data-test': 'stub-login' }, 'login'),
})
const StubPicker = defineComponent({
  name: 'StubPicker',
  setup: () => () => h('div', { 'data-test': 'stub-picker' }, 'picker'),
})
const StubPatient = defineComponent({
  name: 'StubPatient',
  props: { pid: { type: String, required: true } },
  setup: (props) =>
    () => h('div', { 'data-test': 'stub-patient' }, `patient ${props.pid}`),
})

function makeRouter(): Router {
  const routes: RouteRecordRaw[] = [
    {
      path: '/',
      name: 'patient-picker',
      component: StubPicker,
      meta: { requiresAuth: false },
    },
    {
      path: '/patient/:pid',
      name: 'patient',
      component: StubPatient,
      props: true,
      meta: { requiresAuth: false },
    },
    {
      path: '/login',
      name: 'login',
      component: StubLogin,
      meta: { requiresAuth: false },
    },
  ]
  return createRouter({ history: createMemoryHistory(), routes })
}

describe('App.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockWhoamiUnauthenticated()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('mounts without throwing (smoke)', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: { RouterView: true, AgentDrawer: true },
      },
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the AgentDrawer at the App root so it persists across routes', async () => {
    const router = makeRouter()
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: { RouterView: true },
      },
    })
    // AgentDrawer's collapsed-state toggle button is its only DOM
    // imprint when the store is in its initial closed state.
    expect(wrapper.find('[data-test="agent-drawer-toggle"]').exists()).toBe(true)
  })

  it('mirrors /patient/:pid into the drawer store', async () => {
    const router = makeRouter()
    await router.push('/patient/abc-123')
    await router.isReady()

    mount(App, {
      global: { plugins: [router], stubs: { AgentDrawer: true } },
    })
    await flushPromises()

    const store = useAgentDrawer()
    expect(store.activePatient).toBe('abc-123')
  })

  it('clears the active patient when the route leaves /patient/:pid', async () => {
    const router = makeRouter()
    await router.push('/patient/abc-123')
    await router.isReady()

    mount(App, {
      global: { plugins: [router], stubs: { AgentDrawer: true } },
    })
    await flushPromises()

    await router.push('/')
    await flushPromises()

    const store = useAgentDrawer()
    expect(store.activePatient).toBeNull()
  })
})
