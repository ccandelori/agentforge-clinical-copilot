import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentTurn } from '@/composables/useAgentTurn'

// `useAgentTurn` is the dashboard half of the auth bridge described in
// docs/adr/0001-dashboard-auth-bridging.md — POSTs to the sidecar's
// /api/agent/turn route over the BFF session cookie. Tokens never
// touch JS; this composable just shapes the body, calls fetch, and
// surfaces a typed result.

interface FetchSpy extends ReturnType<typeof vi.fn<typeof fetch>> {
  lastRequest?: { url: string; init: RequestInit | undefined }
}

function mockFetch(
  responder: (
    url: string,
    init: RequestInit | undefined,
  ) => Response | Promise<Response>,
): FetchSpy {
  const spy = vi.fn<typeof fetch>(async (input, init) => {
    const url = typeof input === 'string' ? input : (input as URL).toString()
    spy.lastRequest = { url, init }
    return await responder(url, init)
  }) as FetchSpy
  globalThis.fetch = spy as unknown as typeof fetch
  return spy
}

describe('useAgentTurn', () => {
  beforeEach(() => {
    // Clean slate per spec; vi.restoreAllMocks() in afterEach.
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('starts idle with no error', () => {
    const turn = useAgentTurn()
    expect(turn.status.value).toBe('idle')
    expect(turn.error.value).toBeNull()
  })

  it('POSTs to /api/agent/turn with message + patient_id + session_id', async () => {
    const spy = mockFetch(() =>
      new Response(JSON.stringify({ reply: 'pong' }), { status: 200 }),
    )

    const turn = useAgentTurn()
    const reply = await turn.send({
      message: 'ping',
      patient_id: 42,
      session_id: 'chart:42',
    })

    expect(reply).toBe('pong')
    expect(spy.lastRequest?.url).toBe('/api/agent/turn')
    expect(spy.lastRequest?.init?.method).toBe('POST')
    expect(spy.lastRequest?.init?.credentials).toBe('same-origin')
    const headers = new Headers(spy.lastRequest?.init?.headers)
    expect(headers.get('content-type')).toBe('application/json')
    expect(spy.lastRequest?.init?.body).toBe(
      JSON.stringify({
        message: 'ping',
        patient_id: 42,
        session_id: 'chart:42',
      }),
    )
  })

  it('omits session_id from the body when not provided', async () => {
    mockFetch(() =>
      new Response(JSON.stringify({ reply: 'ok' }), { status: 200 }),
    )

    const turn = useAgentTurn()
    await turn.send({ message: 'hi', patient_id: 1 })

    const body = JSON.parse(
      (globalThis.fetch as unknown as FetchSpy).lastRequest?.init?.body as string,
    )
    expect(body).toEqual({ message: 'hi', patient_id: 1 })
  })

  it('flips status loading → success around a successful call', async () => {
    let resolve!: (response: Response) => void
    const pending = new Promise<Response>((r) => {
      resolve = r
    })
    mockFetch(() => pending)

    const turn = useAgentTurn()
    const promise = turn.send({ message: 'q', patient_id: 1 })

    // Loading state visible mid-flight
    await Promise.resolve()
    expect(turn.status.value).toBe('loading')

    resolve(new Response(JSON.stringify({ reply: 'a' }), { status: 200 }))
    await promise

    expect(turn.status.value).toBe('success')
    expect(turn.error.value).toBeNull()
  })

  it('captures error and rethrows on non-2xx', async () => {
    mockFetch(() =>
      new Response(JSON.stringify({ detail: 'kaboom' }), { status: 502 }),
    )

    const turn = useAgentTurn()
    await expect(
      turn.send({ message: 'q', patient_id: 1 }),
    ).rejects.toThrow(/502/)
    expect(turn.status.value).toBe('error')
    expect(turn.error.value).not.toBeNull()
  })

  it('captures error and rethrows on transport failure', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch')
    })

    const turn = useAgentTurn()
    await expect(
      turn.send({ message: 'q', patient_id: 1 }),
    ).rejects.toThrow(/Failed to fetch/)
    expect(turn.status.value).toBe('error')
  })

  it('throws on a 200 with malformed body (no reply field)', async () => {
    mockFetch(() => new Response(JSON.stringify({}), { status: 200 }))

    const turn = useAgentTurn()
    await expect(
      turn.send({ message: 'q', patient_id: 1 }),
    ).rejects.toThrow(/missing reply/)
  })
})
