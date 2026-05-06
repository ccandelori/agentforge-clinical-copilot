import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mockUserManager = {
  signinRedirect: vi.fn(),
  signinRedirectCallback: vi.fn(),
  signoutRedirect: vi.fn(),
  getUser: vi.fn(),
}

vi.mock('@/services/auth/userManager', () => ({
  getUserManager: () => mockUserManager,
  resetUserManagerForTests: () => {},
}))

import { useAuthStore } from '@/stores/auth'

interface FakeUserOverrides {
  access_token?: string
  id_token?: string
  expired?: boolean
}

function fakeUser(overrides: FakeUserOverrides = {}): unknown {
  return {
    access_token: overrides.access_token ?? 'access-token-abc',
    id_token: overrides.id_token ?? 'id-token-xyz',
    refresh_token: 'refresh-token-123',
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    expired: overrides.expired ?? false,
    profile: { sub: '42', fhirUser: 'Patient/42' },
    scope: 'openid offline_access',
    token_type: 'Bearer',
    state: undefined,
  }
}

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('initial state is signed-out', () => {
    const store = useAuthStore()
    expect(store.status).toBe('signed-out')
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
    expect(store.accessToken).toBeNull()
  })

  it('signIn() transitions to signing-in and delegates to UserManager.signinRedirect', async () => {
    mockUserManager.signinRedirect.mockResolvedValueOnce(undefined)
    const store = useAuthStore()
    const pending = store.signIn('/patient/42')
    expect(store.status).toBe('signing-in')
    await pending
    expect(mockUserManager.signinRedirect).toHaveBeenCalledOnce()
    expect(mockUserManager.signinRedirect).toHaveBeenCalledWith({
      state: { targetPath: '/patient/42' },
    })
  })

  it('signIn() omits state when no targetPath provided', async () => {
    mockUserManager.signinRedirect.mockResolvedValueOnce(undefined)
    const store = useAuthStore()
    await store.signIn()
    expect(mockUserManager.signinRedirect).toHaveBeenCalledWith({ state: undefined })
  })

  it('handleCallback() success populates user and transitions signed-in', async () => {
    const u = fakeUser()
    mockUserManager.signinRedirectCallback.mockResolvedValueOnce(u)
    const store = useAuthStore()
    const returned = await store.handleCallback()
    expect(store.status).toBe('signed-in')
    expect(store.isAuthenticated).toBe(true)
    expect(store.accessToken).toBe('access-token-abc')
    expect(store.idToken).toBe('id-token-xyz')
    expect(returned).toBe(u)
  })

  it('handleCallback() failure transitions to error and rethrows', async () => {
    mockUserManager.signinRedirectCallback.mockRejectedValueOnce(new Error('bad authorization code'))
    const store = useAuthStore()
    await expect(store.handleCallback()).rejects.toThrow('bad authorization code')
    expect(store.status).toBe('error')
    expect(store.error?.message).toBe('bad authorization code')
    expect(store.isAuthenticated).toBe(false)
  })

  it('signOut() clears state and calls UserManager.signoutRedirect', async () => {
    mockUserManager.signinRedirectCallback.mockResolvedValueOnce(fakeUser())
    mockUserManager.signoutRedirect.mockResolvedValueOnce(undefined)
    const store = useAuthStore()
    await store.handleCallback()
    expect(store.isAuthenticated).toBe(true)

    await store.signOut()
    expect(store.status).toBe('signed-out')
    expect(store.user).toBeNull()
    expect(store.error).toBeNull()
    expect(mockUserManager.signoutRedirect).toHaveBeenCalledOnce()
  })

  it('hydrate() picks up an existing valid user from session storage', async () => {
    mockUserManager.getUser.mockResolvedValueOnce(fakeUser())
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-in')
    expect(store.isAuthenticated).toBe(true)
  })

  it('hydrate() ignores an expired user', async () => {
    mockUserManager.getUser.mockResolvedValueOnce(fakeUser({ expired: true }))
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-out')
    expect(store.isAuthenticated).toBe(false)
  })

  it('hydrate() with no stored user stays signed-out', async () => {
    mockUserManager.getUser.mockResolvedValueOnce(null)
    const store = useAuthStore()
    await store.hydrate()
    expect(store.status).toBe('signed-out')
  })

  it('hydrate() swallows getUser errors and stays signed-out', async () => {
    mockUserManager.getUser.mockRejectedValueOnce(new Error('storage tamper'))
    const store = useAuthStore()
    await expect(store.hydrate()).resolves.toBeUndefined()
    expect(store.status).toBe('signed-out')
  })

  it('markExpired() transitions to expired and clears the user', async () => {
    mockUserManager.signinRedirectCallback.mockResolvedValueOnce(fakeUser())
    const store = useAuthStore()
    await store.handleCallback()
    expect(store.status).toBe('signed-in')

    store.markExpired()
    expect(store.status).toBe('expired')
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
  })
})
