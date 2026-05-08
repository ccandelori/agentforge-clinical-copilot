import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDocumentUpload } from './useDocumentUpload'

/**
 * Tests for the document-upload composable that backs the file-attach
 * button in {@link AgentChatPane}. The upload itself is multipart
 * `POST /api/agent/upload` to the BFF; the BFF in turn proxies (or
 * brokers) the OpenEMR session-authenticated upload route. Reasons
 * for going via the BFF (versus directly to OpenEMR) are documented
 * in the composable's module docblock.
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

function makeFile(name = 'lab.pdf', mime = 'application/pdf', body = '%PDF-1.4 test'): File {
  return new File([body], name, { type: mime })
}

describe('useDocumentUpload', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('POSTs multipart body with the file and patient_uuid to the BFF route', async () => {
    const { calls, respond } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const file = makeFile()
    const promise = uploadDocument(file, 'patient-uuid-1', 'intake_form')
    respond({ success: true, document_id: 42 }, 201)
    const result = await promise

    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).toBe('/api/agent/upload')
    expect(calls[0]!.init.method).toBe('POST')
    // Same-origin so the BFF cookie rides on the request.
    expect(calls[0]!.init.credentials).toBe('same-origin')
    // FormData body — the browser sets the multipart boundary, we
    // never set Content-Type ourselves.
    const body = calls[0]!.init.body
    expect(body).toBeInstanceOf(FormData)
    const fd = body as FormData
    expect(fd.get('file')).toBeInstanceOf(File)
    expect((fd.get('file') as File).name).toBe('lab.pdf')
    expect(fd.get('patient_uuid')).toBe('patient-uuid-1')
    expect(fd.get('doc_type')).toBe('intake_form')

    expect(result).toEqual({ document_id: '42' })
  })

  it('forwards lab_pdf doc_type when supplied', async () => {
    const { calls, respond } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const promise = uploadDocument(makeFile(), 'p1', 'lab_pdf')
    respond({ success: true, document_id: 9 })
    await promise

    const fd = calls[0]!.init.body as FormData
    expect(fd.get('doc_type')).toBe('lab_pdf')
  })

  it('coerces a numeric document_id from the BFF into a string', async () => {
    const { respond } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const promise = uploadDocument(makeFile(), 'p1', 'intake_form')
    respond({ success: true, document_id: 7 })
    const result = await promise

    expect(result.document_id).toBe('7')
  })

  it('throws when the BFF returns non-2xx', async () => {
    const { respond } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const promise = uploadDocument(makeFile(), 'p1', 'intake_form')
    respond({ error: 'CSRF failed' }, 403)

    await expect(promise).rejects.toThrow(/upload failed/i)
  })

  it('throws when the response payload is missing document_id', async () => {
    const { respond } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const promise = uploadDocument(makeFile(), 'p1', 'intake_form')
    respond({ success: true })

    await expect(promise).rejects.toThrow(/document_id/i)
  })

  it('rejects with a friendly error on network failure', async () => {
    const { fail } = setupFetchMock()
    const { uploadDocument } = useDocumentUpload()

    const promise = uploadDocument(makeFile(), 'p1', 'intake_form')
    fail(new TypeError('network down'))

    await expect(promise).rejects.toThrow()
  })
})
