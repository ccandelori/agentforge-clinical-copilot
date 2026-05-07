import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAgentTurn } from './useAgentTurn'

/**
 * Tests for the BFF agent-turn round-trip composable.
 *
 * Scope here is narrow: shape of the body we send and shape of the
 * result we surface. The session/identity bridging is the sidecar's
 * concern.
 */

interface CapturedRequest {
  url: string
  init: RequestInit
}

function setupFetchMock(): {
  calls: CapturedRequest[]
  respond: (body: unknown, status?: number) => void
} {
  const calls: CapturedRequest[] = []
  let resolve: ((res: Response) => void) | null = null

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    return new Promise<Response>((res) => {
      resolve = res
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    calls,
    respond: (body, statusCode = 200) => {
      const text = typeof body === 'string' ? body : JSON.stringify(body)
      resolve?.(
        new Response(text, {
          status: statusCode,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    },
  }
}

function parseBody(init: RequestInit): Record<string, unknown> {
  const body = init.body
  if (typeof body !== 'string') {
    throw new TypeError('expected JSON string body')
  }
  return JSON.parse(body) as Record<string, unknown>
}

describe('useAgentTurn', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('omits document_id by default', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hello', patient_uuid: 'p1' })
    respond({ reply: 'hi', citations: [] })
    await promise

    expect(calls).toHaveLength(1)
    const body = parseBody(calls[0]!.init)
    expect(body.message).toBe('hello')
    expect(body.patient_uuid).toBe('p1')
    expect('document_id' in body).toBe(false)
  })

  it('forwards document_id when supplied', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({
      message: 'extract this',
      patient_uuid: 'p1',
      document_id: '42',
    })
    respond({ reply: 'ok', citations: [] })
    await promise

    expect(calls).toHaveLength(1)
    const body = parseBody(calls[0]!.init)
    expect(body.document_id).toBe('42')
  })

  it('does not include document_id when callers pass undefined', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({
      message: 'hi',
      patient_uuid: 'p1',
      document_id: undefined,
    })
    respond({ reply: 'ok' })
    await promise

    const body = parseBody(calls[0]!.init)
    expect('document_id' in body).toBe(false)
  })

  it('returns the parsed reply and citations', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({
      reply: 'There you go',
      citations: [
        {
          id: 'note-1',
          source: 'Note 1',
          excerpt: 'foo',
          date: '2026-01-01',
          kind: 'note',
        },
      ],
    })
    const result = await promise

    expect(result.reply).toBe('There you go')
    expect(result.citations).toHaveLength(1)
    expect(result.citations[0]!.id).toBe('note-1')
  })
})
