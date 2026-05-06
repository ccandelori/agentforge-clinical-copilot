import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { User } from 'oidc-client-ts'
import { getUserManager } from '@/services/auth/userManager'

export type AuthStatus = 'signed-out' | 'signing-in' | 'signed-in' | 'expired' | 'error'

export const useAuthStore = defineStore('auth', () => {
  const status = ref<AuthStatus>('signed-out')
  const user = ref<User | null>(null)
  const error = ref<Error | null>(null)

  const isAuthenticated = computed(
    () => status.value === 'signed-in' && user.value !== null && !user.value.expired,
  )
  const accessToken = computed(() => user.value?.access_token ?? null)
  const idToken = computed(() => user.value?.id_token ?? null)
  const profile = computed(() => user.value?.profile ?? null)

  function reset(): void {
    user.value = null
    error.value = null
    status.value = 'signed-out'
  }

  async function hydrate(): Promise<void> {
    try {
      const existing = await getUserManager().getUser()
      if (existing !== null && !existing.expired) {
        user.value = existing
        status.value = 'signed-in'
      }
    } catch {
      // Hydration failures are not fatal — start signed-out.
    }
  }

  async function signIn(targetPath?: string): Promise<void> {
    status.value = 'signing-in'
    error.value = null
    await getUserManager().signinRedirect({
      state: targetPath !== undefined ? { targetPath } : undefined,
    })
  }

  async function handleCallback(): Promise<User> {
    try {
      const result = await getUserManager().signinRedirectCallback()
      user.value = result
      status.value = 'signed-in'
      error.value = null
      return result
    } catch (caught) {
      const e = caught instanceof Error ? caught : new Error(String(caught))
      status.value = 'error'
      error.value = e
      throw e
    }
  }

  async function signOut(): Promise<void> {
    reset()
    await getUserManager().signoutRedirect()
  }

  function markExpired(): void {
    user.value = null
    status.value = 'expired'
  }

  return {
    status,
    user,
    error,
    isAuthenticated,
    accessToken,
    idToken,
    profile,
    reset,
    hydrate,
    signIn,
    handleCallback,
    signOut,
    markExpired,
  }
})
