<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const triggering = ref(false)

const errorMessage = computed<string | null>(() => {
  if (auth.error !== null) return auth.error.message
  const oauthError = route.query.error
  if (typeof oauthError === 'string') {
    const desc = route.query.error_description
    return typeof desc === 'string' && desc !== '' ? `${oauthError}: ${desc}` : oauthError
  }
  return null
})

function signIn(): void {
  triggering.value = true
  const next = typeof route.query.next === 'string' ? route.query.next : undefined
  auth.signIn(next)
}
</script>

<template>
  <main class="container py-5" style="max-width: 32rem">
    <h1 class="h3 mb-3">AgentForge Dashboard</h1>
    <p class="text-muted mb-4">
      Sign in with your OpenEMR credentials. The dashboard talks to the
      AgentForge sidecar BFF, which holds the OAuth2 client credentials
      server-side.
    </p>

    <div v-if="errorMessage !== null" class="alert alert-danger" role="alert">
      <strong>Sign-in failed.</strong> {{ errorMessage }}
    </div>

    <button
      type="button"
      class="btn btn-primary"
      :disabled="triggering"
      @click="signIn"
    >
      <span v-if="triggering">
        <span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>
        Redirecting&hellip;
      </span>
      <span v-else>Sign in with OpenEMR</span>
    </button>
  </main>
</template>
