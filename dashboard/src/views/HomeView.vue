<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

// Placeholder; T38.3 turns this into the patient header + cards. For
// T38.2 (BFF) we surface enough auth UI to verify the sign-in /
// sign-out round-trip during manual testing.
const auth = useAuthStore()

async function signOut(): Promise<void> {
  await auth.signOut()
}
</script>

<template>
  <main class="container py-5">
    <div class="d-flex justify-content-between align-items-start mb-4">
      <div>
        <h1 class="h3">AgentForge Dashboard</h1>
        <p class="text-muted mb-0">
          Vue 3 scaffold — patient header and cards land in T38.3+.
        </p>
      </div>
      <button
        type="button"
        class="btn btn-outline-secondary btn-sm"
        @click="signOut"
      >
        Sign out
      </button>
    </div>

    <dl class="row small text-muted">
      <dt class="col-sm-3">Subject (sub)</dt>
      <dd class="col-sm-9 font-monospace">{{ auth.user?.sub ?? '—' }}</dd>
      <dt class="col-sm-3">Name</dt>
      <dd class="col-sm-9">{{ auth.user?.name ?? '—' }}</dd>
      <dt class="col-sm-3">fhirUser</dt>
      <dd class="col-sm-9 font-monospace">{{ auth.user?.fhir_user ?? '—' }}</dd>
      <dt class="col-sm-3">Status</dt>
      <dd class="col-sm-9">{{ auth.status }}</dd>
    </dl>
  </main>
</template>
