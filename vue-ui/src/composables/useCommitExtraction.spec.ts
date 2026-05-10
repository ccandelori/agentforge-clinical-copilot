import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useCommitExtraction } from './useCommitExtraction'

/**
 * Tests for the BFF intake-promotion composable that backs the
 * "Commit selected to chart" button in {@link ExtractionPanel}.
 *
 * The composable POSTs JSON to `/api/agent/promote/intake`; the BFF
 * mints the user-bound JWT from the session cookie and forwards the
 * accepted-items body to the OpenEMR PHP endpoint. These specs
 * exercise the wire shape and the four observable failure paths.
 */

interface MockFetchCall {
  url: string
  init: RequestInit
}

function setupFetchMock(): {
  calls: MockFetchCall[]
  respond: (body: unknown, status?: number) => void
  fail: (err: Error) => void
} {
  const calls: MockFetchCall[] = []
  let resolve: ((res: Response) => void) | null = null
  let reject: ((err: Error) => void) | null = null

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    return new Promise<Response>((res, rej) => {
      resolve = res
      reject = rej
    })
  })
  vi.stubGlobal('fetch', fetchMock)

  return {
    calls,
    respond: (body, statusCode = 200) => {
      const text = typeof body === 'string' ? body : JSON.stringify(body)
      const res = new Response(text, {
        status: statusCode,
        headers: { 'Content-Type': 'application/json' },
      })
      resolve?.(res)
    },
    fail: (err) => {
      reject?.(err)
    },
  }
}

describe('useCommitExtraction', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('POSTs JSON body to the promote endpoint with the items the caller picked', async () => {
    const { calls, respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    const promise = commit({
      patientUuid: 'patient-uuid-1',
      items: [
        { kind: 'allergy', title: 'Penicillin', details: 'rash' },
        { kind: 'medical_problem', title: 'Type 2 diabetes' },
      ],
      questionnaireResponseId: 'qr-uuid-1',
      documentId: '777',
    })
    respond({ count: 2, promoted: [
      { kind: 'allergy', lists_id: 4001, title: 'Penicillin' },
      { kind: 'medical_problem', lists_id: 4002, title: 'Type 2 diabetes' },
    ] }, 201)
    const result = await promise

    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/agent/promote/intake')
    expect(calls[0]!.init.method).toBe('POST')
    expect(calls[0]!.init.credentials).toBe('same-origin')
    expect(calls[0]!.init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Accept: 'application/json',
    })

    const sentBody = JSON.parse(calls[0]!.init.body as string)
    expect(sentBody.patient_uuid).toBe('patient-uuid-1')
    expect(sentBody.questionnaire_response_id).toBe('qr-uuid-1')
    expect(sentBody.document_id).toBe('777')
    expect(sentBody.items).toEqual([
      { kind: 'allergy', title: 'Penicillin', details: 'rash' },
      { kind: 'medical_problem', title: 'Type 2 diabetes' },
    ])

    expect(result.count).toBe(2)
    expect(result.promoted).toHaveLength(2)
    // snake_case → camelCase mapping at the parser boundary
    expect(result.promoted[0]!.listsId).toBe(4001)
  })

  it('omits empty details fields when the caller supplies an empty string', async () => {
    const { calls, respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    const promise = commit({
      patientUuid: 'p1',
      items: [
        { kind: 'allergy', title: 'Latex', details: '' },
      ],
    })
    respond({ count: 1, promoted: [{ kind: 'allergy', lists_id: 1, title: 'Latex' }] })
    await promise

    const sentBody = JSON.parse(calls[0]!.init.body as string)
    // Empty string details drop entirely so the PHP side sees the
    // field as absent (consistent with the no-details case).
    expect(sentBody.items[0]).toEqual({ kind: 'allergy', title: 'Latex' })
  })

  it('throws synchronously without firing fetch when items is empty', async () => {
    const { calls } = setupFetchMock()
    const { commit, status, error } = useCommitExtraction()

    await expect(
      commit({ patientUuid: 'p1', items: [] }),
    ).rejects.toThrow(/no items selected/i)

    expect(calls).toHaveLength(0)
    expect(status.value).toBe('error')
    expect(error.value?.message).toMatch(/no items selected/i)
  })

  it('dispatches auth:unauthorized on 401 and surfaces a friendly error', async () => {
    const { respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    const eventListener = vi.fn()
    window.addEventListener('auth:unauthorized', eventListener)

    const promise = commit({
      patientUuid: 'p1',
      items: [{ kind: 'allergy', title: 'x' }],
    })
    respond({ error: 'session expired' }, 401)

    await expect(promise).rejects.toThrow(/session expired/i)
    expect(eventListener).toHaveBeenCalledTimes(1)

    window.removeEventListener('auth:unauthorized', eventListener)
  })

  it('throws with an HTTP-status-bearing message on a non-2xx upstream', async () => {
    const { respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    const promise = commit({
      patientUuid: 'p1',
      items: [{ kind: 'allergy', title: 'x' }],
    })
    respond({ error: 'forbidden' }, 502)
    await expect(promise).rejects.toThrow(/HTTP 502/)
  })

  it('parses count from the explicit field, falling back to promoted.length', async () => {
    const { respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    // Drop `count` entirely — composable should derive from promoted[].
    const promise = commit({
      patientUuid: 'p1',
      items: [{ kind: 'allergy', title: 'x' }],
    })
    respond({ promoted: [{ kind: 'allergy', lists_id: 1, title: 'x' }] })
    const result = await promise
    expect(result.count).toBe(1)
  })

  it('drops malformed handle entries so callers see a clean array', async () => {
    const { respond } = setupFetchMock()
    const { commit } = useCommitExtraction()

    const promise = commit({
      patientUuid: 'p1',
      items: [{ kind: 'allergy', title: 'x' }],
    })
    respond({
      count: 3,
      promoted: [
        { kind: 'allergy', lists_id: 1, title: 'good' },
        { kind: 'made_up_kind', lists_id: 2, title: 'bad' },
        { kind: 'medical_problem', title: 'no-id' },
      ],
    })
    const result = await promise
    expect(result.promoted).toHaveLength(1)
    expect(result.promoted[0]!.title).toBe('good')
  })
})
