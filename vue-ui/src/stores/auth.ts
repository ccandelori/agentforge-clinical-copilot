import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { navigateTo } from '@/services/navigation'

/**
 * Auth store — BFF flow (Wave 3 swap, modeled on dashboard-port).
 *
 * The browser never sees an OAuth2 token. The sidecar (FastAPI) holds
 * the client_secret and exchanges code → tokens server-side; the
 * browser holds only an HttpOnly session cookie. This store mirrors
 * that session's authenticated/unauthenticated state and surfaces the
 * user identity claims the sidecar returns from /auth/whoami.
 *
 * No localStorage, no sessionStorage — the HttpOnly cookie is the only
 * thing that persists auth across reloads.
 */

export type AuthStatus = 'unknown' | 'signed-in' | 'signed-out'

/**
 * Identity claims surfaced by the sidecar's /auth/whoami endpoint.
 *
 * NOTE: This is a *different shape* than the legacy mock-data `User`
 * in `@/api/mock` (which had `id/username/fullName/role`). Consumers
 * (AppShell user menu, Settings ProfileSection) need updating during
 * the Wave-3 integration pass.
 */
export interface User {
  sub: string
  name: string | null
  fhir_user: string | null
  email: string | null
}

interface WhoamiAuthenticated {
  authenticated: true
  user: User
  expires_at: number | null
}

interface WhoamiUnauthenticated {
  authenticated: false
}

type WhoamiResponse = WhoamiAuthenticated | WhoamiUnauthenticated

const WHOAMI_TIMEOUT_MS = 5_000

export const useAuthStore = defineStore('auth', () => {
  const status = ref<AuthStatus>('unknown')
  const user = ref<User | null>(null)
  const expiresAt = ref<number | null>(null)
  const error = ref<Error | null>(null)

  const isAuthenticated = computed<boolean>(
    () => status.value === 'signed-in' && user.value !== null,
  )

  /**
   * Probe the sidecar for the current session. Idempotent — safe to
   * call repeatedly; the router guard awaits this once per app load.
   *
   * On 200: derive status from `authenticated` flag.
   * On 401: status='signed-out'.
   * On timeout / network error: status='signed-out' (sidecar unreachable).
   */
  async function hydrate(): Promise<void> {
    // Hard timeout — if /auth/whoami hangs (e.g. the sidecar isn't
    // running), the router guard would block the page render forever.
    // 5s is generous for a same-origin probe.
    const controller = new AbortController()
    const timeoutId = window.setTimeout(
      () => controller.abort(),
      WHOAMI_TIMEOUT_MS,
    )

    try {
      const response = await fetch('/auth/whoami', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      })

      if (response.status === 401) {
        user.value = null
        expiresAt.value = null
        status.value = 'signed-out'
        error.value = null
        return
      }

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
      // Network failure or timeout — treat as signed-out so the guard
      // can redirect to /login (which then surfaces the unreachable
      // state via signIn's hand-off button).
      user.value = null
      expiresAt.value = null
      status.value = 'signed-out'
      const err = caught instanceof Error ? caught : new Error(String(caught))
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

  /**
   * Top-level navigation to the sidecar's OAuth2 entry point. The
   * sidecar will bounce through OpenEMR's authorize endpoint and
   * back to /auth/callback, then redirect to `next`.
   */
  function signIn(next?: string): void {
    const target = next !== undefined && next !== '' ? next : '/dashboard'
    navigateTo(`/auth/login?next=${encodeURIComponent(target)}`)
  }

  /**
   * Tell the sidecar to drop the session cookie, then bounce the user
   * to the login screen. Best-effort on the network call — even if it
   * fails we still clear local state and redirect.
   */
  async function signOut(): Promise<void> {
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
      })
    } catch {
      // Best-effort — see comment above.
    }
    user.value = null
    expiresAt.value = null
    status.value = 'signed-out'
    error.value = null
    navigateTo('/login')
  }

  /**
   * Test/dev convenience — clear local state without a network call.
   */
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
