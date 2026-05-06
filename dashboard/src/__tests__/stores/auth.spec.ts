import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const navigateTo = vi.fn<(url: string) => void>()

vi.mock('@/services/navigation', () => ({
  navigateTo: (url: string) => navigateTo(url),
}))

import { useAuthStore } from '@/stores/auth'

function mockFetchOnce(payload: unknown, init: { ok?: boolean; status?: number } = {}): void {
  const response = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => payload,
  } as unknown as Response
  globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValueOnce(response) as unknown as typeof fetch
}

function mockFetchReject(error: Error): void {
  globalThis.fetch = vi.fn<typeof fetch>().mockRejectedValueOnce(error) as unknown as typeof fetch
}

describe('useAuthStore (BFF flow)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    navigateTo.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initial state is unknown', () => {
    const store = useAuthStore()
    expect(store.status).toBe('unknown')
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })

  it('hydrate() with authenticated whoami sets signed-in', async () => {
    mockFetchOnce({
      authenticated: true,
      user: {
        sub: 'user-1',
        name: 'Dr. Test',
        fhir_user: 'Practitioner/abc',
        email: 'doc@example.org',
      },
      expires_at: 1234567890,
    })
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-in')
    expect(store.isAuthenticated).toBe(true)
    expect(store.user?.sub).toBe('user-1')
    expect(store.user?.fhir_user).toBe('Practitioner/abc')
    expect(store.expiresAt).toBe(1234567890)
  })

  it('hydrate() with authenticated:false sets signed-out', async () => {
    mockFetchOnce({ authenticated: false })
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-out')
    expect(store.isAuthenticated).toBe(false)
    expect(store.user).toBeNull()
  })

  it('hydrate() on network error sets signed-out and surfaces error', async () => {
    mockFetchReject(new Error('connection refused'))
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-out')
    expect(store.error?.message).toBe('connection refused')
  })

  it('hydrate() on 5xx response sets signed-out and captures error', async () => {
    mockFetchOnce({ error: 'BFF down' }, { ok: false, status: 503 })
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-out')
    expect(store.error?.message).toContain('503')
  })

  it('signIn() navigates to /auth/login', () => {
    const store = useAuthStore()
    store.signIn()
    expect(navigateTo).toHaveBeenCalledWith('/auth/login')
  })

  it('signIn(targetPath) encodes ?next=', () => {
    const store = useAuthStore()
    store.signIn('/patient/42?tab=history')
    expect(navigateTo).toHaveBeenCalledWith(
      '/auth/login?next=%2Fpatient%2F42%3Ftab%3Dhistory',
    )
  })

  it('signOut() POSTs /auth/logout, resets state, then navigates to /login', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue({ ok: true, status: 204 } as Response)
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const store = useAuthStore()
    // Pretend we were signed-in
    mockFetchOnce({
      authenticated: true,
      user: { sub: 'u', name: null, fhir_user: null, email: null },
      expires_at: null,
    })
    await store.hydrate()
    expect(store.isAuthenticated).toBe(true)

    // Re-stub fetch for the logout call (the hydrate consumed the
    // first mock).
    globalThis.fetch = fetchMock as unknown as typeof fetch
    await store.signOut()

    expect(fetchMock).toHaveBeenCalledWith(
      '/auth/logout',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(store.status).toBe('signed-out')
    expect(store.user).toBeNull()
    expect(navigateTo).toHaveBeenCalledWith('/login')
  })

  it('signOut() still resets and navigates when /auth/logout fails', async () => {
    globalThis.fetch = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new Error('network')) as unknown as typeof fetch
    const store = useAuthStore()
    // Pretend we were signed-in (without making another fetch call)
    store.$patch({
      status: 'signed-in',
      user: { sub: 'u', name: null, fhir_user: null, email: null },
    } as Partial<ReturnType<typeof useAuthStore>['$state']>)

    await store.signOut()

    expect(store.status).toBe('signed-out')
    expect(store.user).toBeNull()
    expect(navigateTo).toHaveBeenCalledWith('/login')
  })
})
