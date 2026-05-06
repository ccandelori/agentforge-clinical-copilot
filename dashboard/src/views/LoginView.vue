<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const triggering = ref(false)

async function signIn(): Promise<void> {
  triggering.value = true
  const targetPath = typeof route.query.next === 'string' ? route.query.next : undefined
  try {
    await auth.signIn(targetPath)
    // signinRedirect navigates the browser away; this line rarely runs.
  } catch {
    triggering.value = false
  }
}
</script>

<template>
  <main class="container py-5" style="max-width: 32rem">
    <h1 class="h3 mb-3">AgentForge Dashboard</h1>
    <p class="text-muted mb-4">
      Sign in with your OpenEMR credentials to access the patient dashboard.
    </p>

    <div v-if="auth.status === 'error'" class="alert alert-danger" role="alert">
      <strong>Sign-in failed.</strong>
      {{ auth.error?.message ?? 'Unknown error' }}
    </div>
    <div v-else-if="auth.status === 'expired'" class="alert alert-warning" role="alert">
      Your session expired. Please sign in again.
    </div>

    <button
      type="button"
      class="btn btn-primary"
      :disabled="triggering || auth.status === 'signing-in'"
      @click="signIn"
    >
      <span v-if="triggering || auth.status === 'signing-in'">
        <span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>
        Redirecting&hellip;
      </span>
      <span v-else>Sign in with OpenEMR</span>
    </button>
  </main>
</template>
