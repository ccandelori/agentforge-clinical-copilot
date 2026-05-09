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

  it('returns the parsed reply and W2-shape citations', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({
      reply: 'There you go',
      citations: [
        {
          source_type: 'openemr_record',
          source_id: '116',
          page_or_section: '2026-04-12',
          field_or_chunk_id: 'note/116',
          quote_or_value: 'Visit summary text.',
        },
      ],
    })
    const result = await promise

    expect(result.reply).toBe('There you go')
    expect(result.citations).toHaveLength(1)
    const c = result.citations[0]!
    expect(c.source_type).toBe('openemr_record')
    expect(c.source_id).toBe('116')
    expect(c.page_or_section).toBe('2026-04-12')
    expect(c.field_or_chunk_id).toBe('note/116')
    expect(c.quote_or_value).toBe('Visit summary text.')
  })

  it('parses guideline citations with null page_or_section nulls', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'guidelines please' })
    respond({
      reply: 'Per guideline...',
      citations: [
        {
          source_type: 'guideline',
          source_id: 'hypertension-acc-aha-2017-targets',
          page_or_section: 'Blood Pressure Categories in mmHg',
          field_or_chunk_id: 'bp-categories-0',
          quote_or_value: 'Stage 1 HTN: 130-139 mm Hg.',
        },
      ],
    })
    const result = await promise

    expect(result.citations).toHaveLength(1)
    const c = result.citations[0]!
    expect(c.source_type).toBe('guideline')
    expect(c.field_or_chunk_id).toBe('bp-categories-0')
  })

  it('drops citations whose source_type is unknown', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({
      reply: 'ok',
      citations: [
        {
          source_type: 'made_up_kind',
          source_id: 'x',
          page_or_section: null,
          field_or_chunk_id: 'x/y',
          quote_or_value: 'q',
        },
      ],
    })
    const result = await promise

    expect(result.citations).toHaveLength(0)
  })

  it('drops citations missing source_id', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({
      reply: 'ok',
      citations: [
        {
          source_type: 'openemr_record',
          page_or_section: null,
          field_or_chunk_id: 'note/9',
          quote_or_value: 'q',
        },
      ],
    })
    const result = await promise

    expect(result.citations).toHaveLength(0)
  })

  it('coerces null optional fields to nullable on the typed result', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({
      reply: 'ok',
      citations: [
        {
          source_type: 'openemr_record',
          source_id: '9',
          page_or_section: null,
          field_or_chunk_id: null,
          quote_or_value: null,
        },
      ],
    })
    const result = await promise

    expect(result.citations).toHaveLength(1)
    const c = result.citations[0]!
    expect(c.page_or_section).toBeNull()
    expect(c.field_or_chunk_id).toBeNull()
    expect(c.quote_or_value).toBeNull()
  })

  it('surfaces a parsed extraction when the sidecar attaches one', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'extract', document_id: '7' })
    respond({
      reply: 'extracted',
      citations: [],
      extraction: {
        document_id: 7,
        patient_id: 42,
        extraction_confidence: 0.85,
        chief_concern: 'Knee pain',
        chief_concern_citation: {
          source_type: 'intake_form',
          source_id: 'doc-7',
          page_or_section: 'page 1',
          evidence_text: 'Chief: knee pain',
        },
        demographics: [],
        medications: [],
        allergies: [],
        family_history: [],
        unsupported_fields: [],
      },
    })
    const result = await promise

    expect(result.extraction).toBeDefined()
    expect(result.extraction?.documentId).toBe(7)
    expect(result.extraction?.chiefConcern).toBe('Knee pain')
  })

  it('omits extraction when the sidecar surfaces null', async () => {
    const { respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi' })
    respond({ reply: 'no doc attached', citations: [], extraction: null })
    const result = await promise

    expect('extraction' in result).toBe(false)
  })

  // P4 — bug 1 (doc_type) + bug 2 (evidence_query): the BFF accepts both
  // optional fields but the client never sent them. The composable now
  // forwards each only when the caller passes a value; omission keeps
  // the existing chart-Q&A path untouched.

  it('forwards doc_type when supplied', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({
      message: 'extract this',
      patient_uuid: 'p1',
      document_id: '42',
      doc_type: 'lab_pdf',
    })
    respond({ reply: 'ok', citations: [] })
    await promise

    const body = parseBody(calls[0]!.init)
    expect(body.doc_type).toBe('lab_pdf')
  })

  it('omits doc_type by default', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi', patient_uuid: 'p1' })
    respond({ reply: 'ok', citations: [] })
    await promise

    const body = parseBody(calls[0]!.init)
    expect('doc_type' in body).toBe(false)
  })

  it('forwards evidence_query when supplied', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({
      message: 'How should I manage CKD stage 3?',
      patient_uuid: 'p1',
      evidence_query: 'How should I manage CKD stage 3?',
    })
    respond({ reply: 'guidelines say...', citations: [] })
    await promise

    const body = parseBody(calls[0]!.init)
    expect(body.evidence_query).toBe('How should I manage CKD stage 3?')
  })

  it('omits evidence_query by default', async () => {
    const { calls, respond } = setupFetchMock()
    const turn = useAgentTurn()

    const promise = turn.send({ message: 'hi', patient_uuid: 'p1' })
    respond({ reply: 'ok', citations: [] })
    await promise

    const body = parseBody(calls[0]!.init)
    expect('evidence_query' in body).toBe(false)
  })
})
