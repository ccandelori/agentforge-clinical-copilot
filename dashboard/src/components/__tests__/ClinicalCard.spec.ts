import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ClinicalCard from '@/components/ClinicalCard.vue'

describe('<ClinicalCard>', () => {
  it('renders the title', () => {
    const wrapper = mount(ClinicalCard, { props: { title: 'Allergies' } })
    expect(wrapper.text()).toContain('Allergies')
  })

  it('renders the count when provided', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'Allergies', count: 3 },
    })
    expect(wrapper.text()).toContain('Allergies')
    expect(wrapper.text()).toContain('(3)')
  })

  it('omits the count when null', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'Allergies', count: null },
    })
    expect(wrapper.text()).not.toMatch(/\(\d/)
  })

  it('omits the count when undefined', () => {
    const wrapper = mount(ClinicalCard, { props: { title: 'Allergies' } })
    expect(wrapper.text()).not.toMatch(/\(\d/)
  })

  it('renders the default slot when state is ready (or unset)', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X' },
      slots: { default: '<p data-test="body">hello</p>' },
    })
    expect(wrapper.find('[data-test="body"]').exists()).toBe(true)
  })

  it('renders the header-actions slot', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X' },
      slots: { 'header-actions': '<button data-test="action">Edit</button>' },
    })
    expect(wrapper.find('[data-test="action"]').exists()).toBe(true)
  })

  it('shows the default loading state when state="loading"', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'loading' },
    })
    expect(wrapper.text()).toContain('Loading')
    expect(wrapper.find('.spinner-border').exists()).toBe(true)
  })

  it('shows the default empty copy when state="empty"', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'empty' },
    })
    expect(wrapper.text()).toContain('No items')
  })

  it('shows the error message when state="error" and an Error is provided', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'error', error: new Error('fetch blew up') },
    })
    expect(wrapper.text()).toContain('fetch blew up')
  })

  it('falls back to a generic error message when no Error is provided', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'error' },
    })
    expect(wrapper.text()).toContain('Failed to load')
  })

  it('honors a custom #error slot and skips the default rendering', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'error', error: new Error('boom') },
      slots: { error: '<p data-test="custom-err">custom</p>' },
    })
    expect(wrapper.find('[data-test="custom-err"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('boom')
  })

  it('honors a custom #empty slot', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'empty' },
      slots: { empty: '<p data-test="custom-empty">nothing here</p>' },
    })
    expect(wrapper.find('[data-test="custom-empty"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('No items')
  })

  it('hides the body content when in a non-ready state', () => {
    const wrapper = mount(ClinicalCard, {
      props: { title: 'X', state: 'loading' },
      slots: { default: '<p data-test="body">hello</p>' },
    })
    expect(wrapper.find('[data-test="body"]').exists()).toBe(false)
  })

  describe('collapse/expand', () => {
    it('does not render a toggle button when collapsible is false (or unset)', () => {
      const wrapper = mount(ClinicalCard, { props: { title: 'X' } })
      expect(wrapper.find('button[aria-expanded]').exists()).toBe(false)
    })

    it('renders a toggle button when collapsible is true', () => {
      const wrapper = mount(ClinicalCard, {
        props: { title: 'X', collapsible: true },
      })
      const btn = wrapper.find('button[aria-expanded]')
      expect(btn.exists()).toBe(true)
      expect(btn.attributes('aria-expanded')).toBe('true')
    })

    // v-show writes `display: none` to the inline style attribute. We
    // assert against that directly rather than rely on
    // `wrapper.isVisible()` — JSDOM's `getComputedStyle` /
    // `offsetParent` are flaky for v-show ancestors and produce false
    // positives.
    function bodyHidden(wrapper: ReturnType<typeof mount>): boolean {
      const style = wrapper.find('.card-body').element.getAttribute('style')
      return (style ?? '').includes('display: none')
    }

    it('starts expanded by default', () => {
      const wrapper = mount(ClinicalCard, {
        props: { title: 'X', collapsible: true },
        slots: { default: '<p>visible</p>' },
      })
      expect(bodyHidden(wrapper)).toBe(false)
    })

    it('starts collapsed when defaultCollapsed=true', () => {
      const wrapper = mount(ClinicalCard, {
        props: { title: 'X', collapsible: true, defaultCollapsed: true },
        slots: { default: '<p>visible</p>' },
      })
      expect(bodyHidden(wrapper)).toBe(true)
      const btn = wrapper.find('button[aria-expanded]')
      expect(btn.attributes('aria-expanded')).toBe('false')
    })

    it('toggles body visibility on header click', async () => {
      const wrapper = mount(ClinicalCard, {
        props: { title: 'X', collapsible: true },
        slots: { default: '<p>visible</p>' },
      })
      const btn = wrapper.find('button[aria-expanded]')
      expect(btn.attributes('aria-expanded')).toBe('true')
      expect(bodyHidden(wrapper)).toBe(false)
      await btn.trigger('click')
      expect(btn.attributes('aria-expanded')).toBe('false')
      expect(bodyHidden(wrapper)).toBe(true)
      await btn.trigger('click')
      expect(btn.attributes('aria-expanded')).toBe('true')
      expect(bodyHidden(wrapper)).toBe(false)
    })
  })
})
