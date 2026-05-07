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

  it('POSTs to /api/agent/turn with message + patient_uuid + session_id', async () => {
    const spy = mockFetch(() =>
      new Response(JSON.stringify({ reply: 'pong' }), { status: 200 }),
    )

    const turn = useAgentTurn()
    const result = await turn.send({
      message: 'ping',
      patient_uuid: 'patient-uuid-abc',
      session_id: 'chart:patient-uuid-abc',
    })

    expect(result.reply).toBe('pong')
    expect(result.citations).toEqual([])
    expect(spy.lastRequest?.url).toBe('/api/agent/turn')
    expect(spy.lastRequest?.init?.method).toBe('POST')
    expect(spy.lastRequest?.init?.credentials).toBe('same-origin')
    const headers = new Headers(spy.lastRequest?.init?.headers)
    expect(headers.get('content-type')).toBe('application/json')
    expect(spy.lastRequest?.init?.body).toBe(
      JSON.stringify({
        message: 'ping',
        patient_uuid: 'patient-uuid-abc',
        session_id: 'chart:patient-uuid-abc',
      }),
    )
  })

  it('parses citations from the response when sidecar returns them', async () => {
    mockFetch(() =>
      new Response(
        JSON.stringify({
          reply: 'with sources',
          citations: [
            {
              id: 'c-1',
              source: 'Note 2024-09-12',
              excerpt: 'BP 128/78',
              date: '2024-09-12',
              kind: 'note',
              provenance: 'Encounter/abc',
            },
            {
              id: 'c-2',
              source: 'Lab Result',
              excerpt: 'A1C 6.8%',
              date: '2024-08-30',
              kind: 'lab',
            },
            // Unknown kind — should be dropped at the boundary.
            {
              id: 'c-bad',
              source: 'x',
              excerpt: 'x',
              date: 'x',
              kind: 'imaging',
            },
          ],
        }),
        { status: 200 },
      ),
    )

    const turn = useAgentTurn()
    const result = await turn.send({ message: 'q', patient_uuid: 'p' })
    expect(result.reply).toBe('with sources')
    expect(result.citations).toHaveLength(2)
    expect(result.citations[0]?.id).toBe('c-1')
    expect(result.citations[0]?.provenance).toBe('Encounter/abc')
    expect(result.citations[1]?.id).toBe('c-2')
  })

  it('returns an empty citations array when sidecar omits the field', async () => {
    mockFetch(() =>
      new Response(JSON.stringify({ reply: 'no cites' }), { status: 200 }),
    )

    const turn = useAgentTurn()
    const result = await turn.send({ message: 'q', patient_uuid: 'p' })
    expect(result.reply).toBe('no cites')
    expect(result.citations).toEqual([])
  })

  it('omits session_id from the body when not provided', async () => {
    mockFetch(() =>
      new Response(JSON.stringify({ reply: 'ok' }), { status: 200 }),
    )

    const turn = useAgentTurn()
    await turn.send({ message: 'hi', patient_uuid: 'p' })

    const body = JSON.parse(
      (globalThis.fetch as unknown as FetchSpy).lastRequest?.init?.body as string,
    )
    expect(body).toEqual({ message: 'hi', patient_uuid: 'p' })
  })

  it('flips status loading → success around a successful call', async () => {
    let resolve!: (response: Response) => void
    const pending = new Promise<Response>((r) => {
      resolve = r
    })
    mockFetch(() => pending)

    const turn = useAgentTurn()
    const promise = turn.send({ message: 'q', patient_uuid: 'p' })

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
      turn.send({ message: 'q', patient_uuid: 'p' }),
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
      turn.send({ message: 'q', patient_uuid: 'p' }),
    ).rejects.toThrow(/Failed to fetch/)
    expect(turn.status.value).toBe('error')
  })

  it('throws on a 200 with malformed body (no reply field)', async () => {
    mockFetch(() => new Response(JSON.stringify({}), { status: 200 }))

    const turn = useAgentTurn()
    await expect(
      turn.send({ message: 'q', patient_uuid: 'p' }),
    ).rejects.toThrow(/missing reply/)
  })
})
