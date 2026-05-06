import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import App from '@/App.vue'

// Smoke test: confirms the Vitest + jsdom + Vue Test Utils + @-path-alias
// pipeline is wired correctly. The substantive component tests land
// alongside the cards (T38.4–T38.9), drawer (T38.10), and overlay (T38.11).
describe('App.vue (scaffold smoke)', () => {
  it('mounts without throwing', () => {
    const wrapper = mount(App, {
      global: {
        stubs: { RouterView: true },
      },
    })
    expect(wrapper.exists()).toBe(true)
  })
})
