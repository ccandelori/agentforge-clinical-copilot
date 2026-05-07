<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import BaseButton from '@/components/ui/BaseButton.vue'
import { useAuthStore } from '@/stores/auth'

// BFF "Sign in with OpenEMR" hand-off screen. We never collect a
// username or password here — the user is bounced to the sidecar's
// /auth/login route which does the OAuth2 dance against OpenEMR and
// drops a same-origin HttpOnly session cookie on return.

const route = useRoute()
const auth = useAuthStore()

const handingOff = ref<boolean>(false)

const redirectTarget = computed<string>(() => {
  const raw = route.query.redirect
  if (typeof raw !== 'string' || raw.length === 0) return '/dashboard'
  // Only allow same-origin paths — guard against open-redirect.
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/dashboard'
  return raw
})

const authFailed = computed<boolean>(() => route.query.error === 'auth_failed')

const sidecarUnreachable = computed<boolean>(
  () => auth.status === 'signed-out' && auth.error !== null,
)

function onSignIn(): void {
  if (handingOff.value) return
  handingOff.value = true
  // signIn() does a top-level window.location.assign — we never
  // come back to this component after this call.
  auth.signIn(redirectTarget.value)
}
</script>

<template>
  <div
    class="relative flex min-h-screen items-center justify-center overflow-hidden bg-surface-2 px-4 py-10"
  >
    <!-- Decorative gradient backdrop -->
    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary-50 via-surface-2 to-info-50 dark:from-primary-900/40 dark:via-neutral-950 dark:to-info-900/30"
    />
    <div
      aria-hidden="true"
      class="pointer-events-none absolute -left-24 -top-24 h-72 w-72 rounded-full bg-primary-300/40 blur-3xl dark:bg-primary-700/20"
    />
    <div
      aria-hidden="true"
      class="pointer-events-none absolute -bottom-32 -right-20 h-80 w-80 rounded-full bg-info-300/40 blur-3xl dark:bg-info-700/20"
    />
    <div
      aria-hidden="true"
      class="pointer-events-none absolute inset-0 opacity-[0.035] dark:opacity-[0.05]"
      style="
        background-image:
          linear-gradient(to right, currentColor 1px, transparent 1px),
          linear-gradient(to bottom, currentColor 1px, transparent 1px);
        background-size: 32px 32px;
      "
    />

    <main
      class="relative z-10 w-full max-w-md"
      aria-labelledby="login-title"
    >
      <!-- Brand -->
      <div class="mb-6 flex flex-col items-center gap-3 text-center">
        <div
          aria-hidden="true"
          class="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-600 text-white shadow-card-lg"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="currentColor"
            class="h-7 w-7"
          >
            <path
              d="M11 3a1 1 0 0 1 2 0v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V3z"
            />
          </svg>
        </div>
        <div>
          <h1
            id="login-title"
            class="text-xl font-semibold tracking-tight text-ink"
          >
            OpenEMR
          </h1>
          <p class="mt-1 text-sm text-ink-muted">
            Clinical Co-Pilot
          </p>
        </div>
      </div>

      <!-- Card -->
      <section
        class="rounded-2xl border border-line bg-surface p-6 shadow-card-lg sm:p-8"
      >
        <div class="flex flex-col gap-4 text-center">
          <p class="text-sm text-ink-muted">
            You'll be redirected to OpenEMR to sign in. We never see your
            password — the session is brokered server-side over an HttpOnly
            cookie.
          </p>

          <!-- Auth-failed banner (sidecar bounced you back with ?error=auth_failed) -->
          <div
            v-if="authFailed && sidecarUnreachable"
            role="alert"
            aria-live="polite"
            class="flex items-start gap-2 rounded-lg border border-danger-500/40 bg-danger-50 px-3 py-2 text-left text-sm text-danger-700 dark:bg-danger-700/10 dark:text-danger-500"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              class="mt-0.5 h-4 w-4 flex-shrink-0"
              aria-hidden="true"
            >
              <path
                fill-rule="evenodd"
                d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0zm-8-3a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 7zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2z"
                clip-rule="evenodd"
              />
            </svg>
            <span>Sign-in unavailable. Please try again in a moment.</span>
          </div>

          <BaseButton
            type="button"
            variant="primary"
            size="lg"
            block
            :loading="handingOff"
            :disabled="handingOff"
            @click="onSignIn"
          >
            {{ handingOff ? 'Redirecting…' : 'Sign in with OpenEMR' }}
          </BaseButton>
        </div>
      </section>

      <p class="mt-6 text-center text-xs text-ink-muted">
        AgentForge · Clinical Co-Pilot
      </p>
    </main>
  </div>
</template>
