import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentForgeStore } from './agentforge'

/**
 * Tests for the AgentForge store's request-shaping behaviour around
 * the P4 punch-list bugs:
 *
 *   1. ``doc_type`` rides the turn whenever a {@link PendingAttachment}
 *      is set, so lab PDFs route through ``LAB_CONTRACT`` instead of
 *      defaulting to intake.
 *   2. ``evidence_query`` rides the turn iff the clinician toggled the
 *      "Ask guidelines" mode on. Without the toggle no field is sent so
 *      chart-Q&A turns don't fire the W2 evidence retriever.
 */

// Stub vue-router so the setup store's ``useRoute()`` call resolves to a
// known fixture. Default fixture is the patient-dashboard route so the
// store's ``currentPatientUuid()`` derives a non-empty value.
vi.mock('vue-router', () => ({
  useRoute: () => ({
    name: 'patient-dashboard',
    params: { id: 'patient-uuid-fixture' },
  }),
}))

interface CapturedRequest {
  url: string
  init: RequestInit
}

function setupFetchMock(): {
  calls: CapturedRequest[]
  respondNext: (body: unknown, status?: number) => void
} {
  const calls: CapturedRequest[] = []
  const queue: Array<(res: Response) => void> = []
  const responseQueue: Array<{ body: unknown; status: number }> = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    return new Promise<Response>((resolve) => {
      // If a response was queued before fetch was invoked, fire it
      // immediately. Otherwise stash the resolver for ``respondNext``.
      const next = responseQueue.shift()
      if (next !== undefined) {
        const text =
          typeof next.body === 'string' ? next.body : JSON.stringify(next.body)
        resolve(
          new Response(text, {
            status: next.status,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
        return
      }
      queue.push(resolve)
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  function respondNext(body: unknown, statusCode = 200): void {
    const resolve = queue.shift()
    if (resolve === undefined) {
      // Fetch hasn't been called yet; queue the response so the next
      // call resolves with it.
      responseQueue.push({ body, status: statusCode })
      return
    }
    const text = typeof body === 'string' ? body : JSON.stringify(body)
    resolve(
      new Response(text, {
        status: statusCode,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  return { calls, respondNext }
}

function parseBody(init: RequestInit): Record<string, unknown> {
  const body = init.body
  if (typeof body !== 'string') {
    throw new TypeError('expected JSON string body')
  }
  return JSON.parse(body) as Record<string, unknown>
}

describe('useAgentForgeStore — turn request shaping', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    if (typeof sessionStorage !== 'undefined') sessionStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('omits doc_type when no attachment is pending', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()

    const send = store.sendMessage('hi there')
    respondNext({ reply: 'ok', citations: [] })
    await send

    expect(calls).toHaveLength(1)
    const body = parseBody(calls[0]!.init)
    expect('doc_type' in body).toBe(false)
    expect('document_id' in body).toBe(false)
  })

  it('forwards doc_type=lab_pdf when a lab attachment is pending', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()
    store.setPendingAttachment({
      documentId: '99',
      filename: 'cbc-results.pdf',
      docType: 'lab_pdf',
    })

    const send = store.sendMessage('extract this')
    respondNext({ reply: 'ok', citations: [] })
    await send

    expect(calls).toHaveLength(1)
    const body = parseBody(calls[0]!.init)
    expect(body.doc_type).toBe('lab_pdf')
    expect(body.document_id).toBe('99')
  })

  it('forwards doc_type=intake_form when an intake attachment is pending', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()
    store.setPendingAttachment({
      documentId: '11',
      filename: 'new-patient-intake.pdf',
      docType: 'intake_form',
    })

    const send = store.sendMessage('extract this')
    respondNext({ reply: 'ok', citations: [] })
    await send

    const body = parseBody(calls[0]!.init)
    expect(body.doc_type).toBe('intake_form')
    expect(body.document_id).toBe('11')
  })

  it('clears the pending attachment after sending so doc_type rides exactly one turn', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()
    store.setPendingAttachment({
      documentId: '11',
      filename: 'cbc.pdf',
      docType: 'lab_pdf',
    })

    const send1 = store.sendMessage('first')
    respondNext({ reply: 'one', citations: [] })
    await send1

    const send2 = store.sendMessage('second')
    respondNext({ reply: 'two', citations: [] })
    await send2

    expect(calls).toHaveLength(2)
    const body2 = parseBody(calls[1]!.init)
    expect('doc_type' in body2).toBe(false)
    expect('document_id' in body2).toBe(false)
    expect(store.pendingAttachment).toBeNull()
  })

  it('omits evidence_query by default (chart-Q&A path)', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()

    const send = store.sendMessage('show abnormal labs')
    respondNext({ reply: 'ok', citations: [] })
    await send

    const body = parseBody(calls[0]!.init)
    expect('evidence_query' in body).toBe(false)
  })

  it('forwards evidence_query when guideline mode is on', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()
    store.setGuidelineMode(true)

    const send = store.sendMessage('How do I manage CKD stage 3?')
    respondNext({ reply: 'guidelines say...', citations: [] })
    await send

    const body = parseBody(calls[0]!.init)
    expect(body.evidence_query).toBe('How do I manage CKD stage 3?')
    // ``message`` stays populated — the orchestrator inspects both.
    expect(body.message).toBe('How do I manage CKD stage 3?')
  })

  it('toggling guideline mode off again returns to chart-Q&A behaviour', async () => {
    const { calls, respondNext } = setupFetchMock()
    const store = useAgentForgeStore()
    store.setGuidelineMode(true)
    store.setGuidelineMode(false)

    const send = store.sendMessage('show abnormal labs')
    respondNext({ reply: 'ok', citations: [] })
    await send

    const body = parseBody(calls[0]!.init)
    expect('evidence_query' in body).toBe(false)
  })

  it('exposes guidelineMode as reactive state', () => {
    const store = useAgentForgeStore()
    expect(store.guidelineMode).toBe(false)
    store.setGuidelineMode(true)
    expect(store.guidelineMode).toBe(true)
    store.setGuidelineMode(false)
    expect(store.guidelineMode).toBe(false)
  })
})
