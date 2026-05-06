import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { navigateTo } from '@/services/navigation'

// Auth state for the BFF flow (T38.2 v2). The dashboard never sees
// OAuth2 tokens — those live server-side in the sidecar's session
// store, keyed by an HttpOnly session cookie. This store mirrors the
// session's authenticated/unauthenticated state and surfaces the
// user identity claims the sidecar returns from /auth/whoami.

export type AuthStatus = 'unknown' | 'signed-in' | 'signed-out'

export interface DashboardUser {
  sub: string
  name: string | null
  fhir_user: string | null
  email: string | null
}

interface WhoamiAuthenticated {
  authenticated: true
  user: DashboardUser
  expires_at: number | null
}

interface WhoamiUnauthenticated {
  authenticated: false
}

type WhoamiResponse = WhoamiAuthenticated | WhoamiUnauthenticated

export const useAuthStore = defineStore('auth', () => {
  const status = ref<AuthStatus>('unknown')
  const user = ref<DashboardUser | null>(null)
  const expiresAt = ref<number | null>(null)
  const error = ref<Error | null>(null)

  const isAuthenticated = computed(
    () => status.value === 'signed-in' && user.value !== null,
  )

  async function hydrate(): Promise<void> {
    // Hard timeout — if /auth/whoami hangs (e.g. the sidecar isn't
    // running), we'd blank the page indefinitely because the router
    // guard awaits this. 5s is generous for a same-origin probe.
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), 5_000)

    try {
      const response = await fetch('/auth/whoami', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })
      if (!response.ok) {
        throw new Error(`/auth/whoami returned ${response.status}`)
      }
      const data = (await response.json()) as WhoamiResponse
      if (data.authenticated) {
        user.value = data.user
        expiresAt.value = data.expires_at
        status.value = 'signed-in'
        error.value = null
      } else {
        user.value = null
        expiresAt.value = null
        status.value = 'signed-out'
        error.value = null
      }
    } catch (caught) {
      user.value = null
      expiresAt.value = null
      status.value = 'signed-out'
      const err = caught instanceof Error ? caught : new Error(String(caught))
      // Friendlier message for the timeout case (most common cause: the
      // sidecar BFF isn't running); the LoginView surfaces this directly.
      if (err.name === 'AbortError') {
        error.value = new Error(
          'Sign-in service unreachable (timed out after 5s). Is the AgentForge sidecar running?',
        )
      } else {
        error.value = err
      }
    } finally {
      window.clearTimeout(timeoutId)
    }
  }

  function signIn(targetPath?: string): void {
    const next =
      targetPath !== undefined && targetPath !== ''
        ? `?next=${encodeURIComponent(targetPath)}`
        : ''
    navigateTo(`/auth/login${next}`)
  }

  async function signOut(): Promise<void> {
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
      })
    } catch {
      // Best-effort — even if the network call fails we still clear
      // local state and bounce the user to /login.
    }
    user.value = null
    expiresAt.value = null
    status.value = 'signed-out'
    error.value = null
    navigateTo('/login')
  }

  function reset(): void {
    user.value = null
    expiresAt.value = null
    status.value = 'signed-out'
    error.value = null
  }

  return {
    status,
    user,
    expiresAt,
    error,
    isAuthenticated,
    hydrate,
    signIn,
    signOut,
    reset,
  }
})
