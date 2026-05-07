<script setup lang="ts">
import { computed, onMounted, ref, useTemplateRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const username = ref<string>('')
const password = ref<string>('')
const showPassword = ref<boolean>(false)
const submitting = ref<boolean>(false)
const errorMessage = ref<string>('')

const usernameInput = useTemplateRef<InstanceType<typeof BaseInput>>('usernameInput')

const passwordType = computed<string>(() =>
  showPassword.value ? 'text' : 'password',
)

const redirectTarget = computed<string>(() => {
  const raw = route.query.redirect
  if (typeof raw !== 'string' || raw.length === 0) return '/dashboard'
  // Only allow same-origin paths to avoid open-redirect funny business.
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/dashboard'
  return raw
})

function focusUsername(): void {
  // BaseInput is a wrapper; the actual <input> is the first descendant.
  const root = usernameInput.value?.$el as HTMLElement | undefined
  const native = root?.querySelector?.('input')
  native?.focus()
}

onMounted(() => {
  // If the user is already authenticated, skip the form.
  if (auth.isAuthenticated) {
    void router.replace(redirectTarget.value)
    return
  }
  focusUsername()
})

function fillDemoCredentials(): void {
  username.value = 'admin'
  password.value = 'pass'
  errorMessage.value = ''
}

function togglePasswordVisibility(): void {
  showPassword.value = !showPassword.value
}

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  errorMessage.value = ''

  if (username.value.trim().length === 0 || password.value.length === 0) {
    errorMessage.value = 'Enter your username and password to continue.'
    return
  }

  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    await router.replace(redirectTarget.value)
  } catch (err) {
    errorMessage.value
      = err instanceof Error && err.message.length > 0
        ? err.message
        : 'Sign in failed. Please try again.'
    password.value = ''
  } finally {
    submitting.value = false
  }
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
            <span class="text-ink-muted">·</span>
            <span class="text-primary-600 dark:text-primary-400">Vue Edition</span>
          </h1>
          <p class="mt-1 text-sm text-ink-muted">
            Sign in to access patient charts and your schedule.
          </p>
        </div>
      </div>

      <!-- Card -->
      <section
        class="rounded-2xl border border-line bg-surface p-6 shadow-card-lg sm:p-8"
      >
        <form
          class="flex flex-col gap-4"
          novalidate
          @submit.prevent="onSubmit"
        >
          <BaseInput
            ref="usernameInput"
            v-model="username"
            label="Username"
            type="text"
            placeholder="e.g. dr_smith"
            autocomplete="username"
            :disabled="submitting"
            required
          />

          <BaseInput
            v-model="password"
            label="Password"
            :type="passwordType"
            placeholder="Your password"
            autocomplete="current-password"
            :disabled="submitting"
            required
          >
            <template #suffix>
              <button
                type="button"
                class="-mr-1 rounded px-2 py-0.5 text-xs font-medium text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 disabled:cursor-not-allowed disabled:opacity-50"
                :aria-label="showPassword ? 'Hide password' : 'Show password'"
                :aria-pressed="showPassword"
                :disabled="submitting"
                @click="togglePasswordVisibility"
              >
                {{ showPassword ? 'Hide' : 'Show' }}
              </button>
            </template>
          </BaseInput>

          <!-- Error alert -->
          <div
            v-if="errorMessage"
            role="alert"
            aria-live="polite"
            class="flex items-start gap-2 rounded-lg border border-danger-500/40 bg-danger-50 px-3 py-2 text-sm text-danger-700 dark:bg-danger-700/10 dark:text-danger-500"
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
            <span>{{ errorMessage }}</span>
          </div>

          <div class="flex items-center justify-between text-xs">
            <button
              type="button"
              class="font-medium text-primary-600 hover:text-primary-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 dark:text-primary-400 dark:hover:text-primary-300"
              @click="fillDemoCredentials"
            >
              Use demo credentials
            </button>
            <a
              href="#"
              class="font-medium text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40"
              @click.prevent
            >
              Forgot password?
            </a>
          </div>

          <BaseButton
            type="submit"
            variant="primary"
            size="lg"
            block
            :loading="submitting"
            :disabled="submitting"
          >
            {{ submitting ? 'Signing in…' : 'Sign in' }}
          </BaseButton>
        </form>
      </section>

      <p class="mt-6 text-center text-xs text-ink-muted">
        Demo build · backed by the FHIR API on Wave 3.
      </p>
    </main>
  </div>
</template>
