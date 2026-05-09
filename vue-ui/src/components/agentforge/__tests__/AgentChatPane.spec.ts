import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentChatPane from '../AgentChatPane.vue'
import { useAgentForgeStore } from '@/stores/agentforge'

/**
 * Pane-level tests scoped to the P4 punch-list bugs:
 *
 *   - Upload-and-attach stamps the inferred ``docType`` onto the
 *     {@link PendingAttachment}, not just the document id.
 *   - The "Ask guidelines" toggle flips ``guidelineMode`` on the store.
 *
 * The chat surface itself is exercised in higher-level integration
 * tests; here we only assert the new wiring around the punch fixes.
 */

vi.mock('vue-router', () => ({
  useRoute: () => ({
    name: 'patient-dashboard',
    params: { id: 'patient-uuid-fixture' },
  }),
}))

// useDocumentUpload is mocked so the test does not have to fight the
// fetch boundary just to land a successful upload.
vi.mock('@/composables/useDocumentUpload', async () => {
  const actual = await vi.importActual<
    typeof import('@/composables/useDocumentUpload')
  >('@/composables/useDocumentUpload')
  const { ref } = await import('vue')
  return {
    ...actual,
    useDocumentUpload: () => ({
      isUploading: ref(false),
      error: ref(null),
      uploadDocument: vi.fn(async () => ({ document_id: '42' })),
    }),
  }
})

function makeFile(name: string): File {
  return new File(['%PDF-1.4 test'], name, { type: 'application/pdf' })
}

describe('AgentChatPane — P4 punch fixes', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    if (typeof sessionStorage !== 'undefined') sessionStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('stamps docType=lab_pdf on the pending attachment for lab filenames', async () => {
    const wrapper = mount(AgentChatPane)
    const store = useAgentForgeStore()
    const fileInput = wrapper.find('[data-test="file-input"]')
      .element as HTMLInputElement

    Object.defineProperty(fileInput, 'files', {
      value: [makeFile('cbc-results.pdf')],
      configurable: true,
    })
    await wrapper.find('[data-test="file-input"]').trigger('change')
    await flushPromises()

    expect(store.pendingAttachment).not.toBeNull()
    expect(store.pendingAttachment?.docType).toBe('lab_pdf')
    expect(store.pendingAttachment?.filename).toBe('cbc-results.pdf')
  })

  it('stamps docType=intake_form for non-lab filenames', async () => {
    const wrapper = mount(AgentChatPane)
    const store = useAgentForgeStore()
    const fileInput = wrapper.find('[data-test="file-input"]')
      .element as HTMLInputElement

    Object.defineProperty(fileInput, 'files', {
      value: [makeFile('new-patient-intake.pdf')],
      configurable: true,
    })
    await wrapper.find('[data-test="file-input"]').trigger('change')
    await flushPromises()

    expect(store.pendingAttachment?.docType).toBe('intake_form')
  })

  it('renders a guideline-mode toggle that flips guidelineMode on the store', async () => {
    const wrapper = mount(AgentChatPane)
    const store = useAgentForgeStore()

    const toggle = wrapper.find('[data-test="guideline-toggle"]')
    expect(toggle.exists()).toBe(true)
    expect(store.guidelineMode).toBe(false)

    await toggle.trigger('click')
    expect(store.guidelineMode).toBe(true)

    await toggle.trigger('click')
    expect(store.guidelineMode).toBe(false)
  })

  it('reflects guideline mode as aria-pressed on the toggle', async () => {
    const wrapper = mount(AgentChatPane)
    const toggle = wrapper.find('[data-test="guideline-toggle"]')

    expect(toggle.attributes('aria-pressed')).toBe('false')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-pressed')).toBe('true')
  })
})
