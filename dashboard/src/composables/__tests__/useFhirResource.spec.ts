import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { useFhirResource } from '@/composables/useFhirResource'

interface PatientLike {
  resourceType: 'Patient'
  id: string
}

function mockFetchResolved(
  payload: unknown,
  init: { ok?: boolean; status?: number } = {},
): void {
  const response = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => payload,
  } as unknown as Response
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(response) as unknown as typeof fetch
}

function mockFetchRejected(error: Error): void {
  globalThis.fetch = vi.fn<typeof fetch>().mockRejectedValue(error) as unknown as typeof fetch
}

describe('useFhirResource', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('flips to loading immediately on creation', () => {
    mockFetchResolved({ resourceType: 'Patient', id: 'abc' })
    const { status } = useFhirResource<PatientLike>('/api/fhir/Patient/abc')
    // Before any microtask drains, status has already been set by the
    // synchronous prefix of refetch().
    expect(status.value).toBe('loading')
  })

  it('auto-fetches on creation and resolves to success', async () => {
    mockFetchResolved({ resourceType: 'Patient', id: 'abc' })
    const { status, data, error } = useFhirResource<PatientLike>('/api/fhir/Patient/abc')
    await flushPromises()
    expect(status.value).toBe('success')
    expect(data.value).toEqual({ resourceType: 'Patient', id: 'abc' })
    expect(error.value).toBeNull()
  })

  it('uses Accept: application/fhir+json with same-origin credentials', async () => {
    mockFetchResolved({ resourceType: 'Patient', id: 'x' })
    useFhirResource<PatientLike>('/api/fhir/Patient/x')
    await flushPromises()
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/fhir/Patient/x', {
      credentials: 'same-origin',
      headers: { Accept: 'application/fhir+json' },
    })
  })

  it('captures error status on non-ok response', async () => {
    mockFetchResolved({ issue: 'not found' }, { ok: false, status: 404 })
    const { status, data, error } = useFhirResource<PatientLike>('/api/fhir/Patient/missing')
    await flushPromises()
    expect(status.value).toBe('error')
    expect(data.value).toBeNull()
    expect(error.value?.message).toContain('404')
  })

  it('captures network errors', async () => {
    mockFetchRejected(new Error('connection refused'))
    const { status, data, error } = useFhirResource<PatientLike>('/api/fhir/Patient/x')
    await flushPromises()
    expect(status.value).toBe('error')
    expect(data.value).toBeNull()
    expect(error.value?.message).toBe('connection refused')
  })

  it('refetch() re-fires the request', async () => {
    mockFetchResolved({ resourceType: 'Patient', id: 'abc' })
    const { refetch } = useFhirResource<PatientLike>('/api/fhir/Patient/abc')
    await flushPromises()
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    await refetch()
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('refetch() clears the prior error on a successful retry', async () => {
    mockFetchRejected(new Error('first try'))
    const result = useFhirResource<PatientLike>('/api/fhir/Patient/abc')
    await flushPromises()
    expect(result.status.value).toBe('error')
    expect(result.error.value?.message).toBe('first try')

    mockFetchResolved({ resourceType: 'Patient', id: 'abc' })
    await result.refetch()
    expect(result.status.value).toBe('success')
    expect(result.error.value).toBeNull()
    expect(result.data.value).toEqual({ resourceType: 'Patient', id: 'abc' })
  })
})
