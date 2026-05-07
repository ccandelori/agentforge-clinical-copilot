import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import type { User } from '@/api/mock'

/**
 * Auth store (Wave 2a).
 *
 * Holds the currently signed-in user and exposes login/logout actions
 * backed by an in-memory whitelist. The session is persisted to
 * `sessionStorage` under `openemr-vue-session` so a page refresh keeps
 * the user signed in for the lifetime of the tab.
 *
 * Wave 3 will swap this for a real OAuth2 client against the OpenEMR
 * FHIR API; the surface (`user`, `isAuthenticated`, `login`, `logout`)
 * should remain stable.
 */

const SESSION_STORAGE_KEY = 'openemr-vue-session'

interface Credential {
  readonly username: string
  readonly password: string
  readonly user: User
}

const CREDENTIALS: readonly Credential[] = [
  {
    username: 'admin',
    password: 'pass',
    user: {
      id: 'u-admin',
      username: 'admin',
      fullName: 'Ada Admin',
      role: 'admin',
    },
  },
  {
    username: 'dr_smith',
    password: 'pass',
    user: {
      id: 'u-dr-smith',
      username: 'dr_smith',
      fullName: 'Dr. Eleanor Smith',
      role: 'physician',
    },
  },
  {
    username: 'nurse_jane',
    password: 'pass',
    user: {
      id: 'u-nurse-jane',
      username: 'nurse_jane',
      fullName: 'Jane Park, RN',
      role: 'nurse',
    },
  },
]

function isUser(value: unknown): value is User {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  if (typeof v.id !== 'string') return false
  if (typeof v.username !== 'string') return false
  if (typeof v.fullName !== 'string') return false
  if (
    v.role !== 'physician'
    && v.role !== 'nurse'
    && v.role !== 'admin'
    && v.role !== 'staff'
  ) {
    return false
  }
  return true
}

function readPersistedUser(): User | null {
  if (typeof sessionStorage === 'undefined') return null
  const raw = sessionStorage.getItem(SESSION_STORAGE_KEY)
  if (raw === null) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    return isUser(parsed) ? parsed : null
  } catch {
    return null
  }
}

function writePersistedUser(user: User | null): void {
  if (typeof sessionStorage === 'undefined') return
  if (user === null) {
    sessionStorage.removeItem(SESSION_STORAGE_KEY)
    return
  }
  sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(user))
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const hydrated = ref<boolean>(false)

  /**
   * Restore any persisted session from sessionStorage. Idempotent —
   * called automatically by `isAuthenticated`/`login`/`logout` so
   * callers do not need to wire it up explicitly.
   */
  function hydrate(): void {
    if (hydrated.value) return
    hydrated.value = true
    user.value = readPersistedUser()
  }

  const isAuthenticated = computed<boolean>(() => {
    if (!hydrated.value) hydrate()
    return user.value !== null
  })

  async function login(username: string, password: string): Promise<void> {
    if (!hydrated.value) hydrate()

    // Tiny artificial delay so callers see a real loading state.
    await new Promise<void>((resolve) => setTimeout(resolve, 250))

    const match = CREDENTIALS.find(
      (c) => c.username === username && c.password === password,
    )
    if (match === undefined) {
      throw new Error('Invalid credentials')
    }
    user.value = match.user
    writePersistedUser(match.user)
  }

  function logout(): void {
    if (!hydrated.value) hydrate()
    user.value = null
    writePersistedUser(null)
  }

  return { user, isAuthenticated, login, logout, hydrate }
})
