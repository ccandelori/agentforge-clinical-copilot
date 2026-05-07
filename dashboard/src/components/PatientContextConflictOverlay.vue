<script setup lang="ts">
import { computed } from 'vue'

import { useAgentDrawer } from '@/stores/agentDrawer'

// Hard-interrupt overlay shown over the chat body when the active
// patient changes mid-Chart-conversation. Three resolution buttons:
//   * Switch  → resolvePatientChange('switch')
//   * Stay    → resolvePatientChange('stay')
//   * Fresh   → resolvePatientChange('fresh') — only when the target
//               already has stale chart history.
//
// While this overlay is visible the input + send button in
// AgentDrawer are disabled (driven off store.pendingPatientChange).

const store = useAgentDrawer()

const pending = computed(() => store.pendingPatientChange)
const hasStaleTarget = computed<boolean>(() =>
  pending.value !== null && store.hasStaleConversation(pending.value.to),
)
</script>

<template>
  <div
    v-if="pending !== null"
    class="patient-conflict-overlay position-absolute top-0 start-0 w-100 h-100 bg-white d-flex align-items-center justify-content-center p-4"
    role="alertdialog"
    aria-modal="true"
    aria-labelledby="patient-conflict-title"
    data-test="patient-conflict-overlay"
  >
    <div class="text-center" style="max-width: 22rem;">
      <h2
        id="patient-conflict-title"
        class="h6 mb-2 text-body-secondary text-uppercase"
      >
        Active chart changed
      </h2>
      <p class="mb-4">
        <code>{{ pending.from }}</code>
        <i class="bi bi-arrow-right mx-2" aria-hidden="true"></i>
        <code>{{ pending.to }}</code>
      </p>
      <div class="d-grid gap-2">
        <button
          type="button"
          class="btn btn-primary"
          data-test="patient-conflict-switch"
          @click="store.resolvePatientChange('switch')"
        >
          Switch to {{ pending.to }}'s conversation
        </button>
        <button
          type="button"
          class="btn btn-outline-secondary"
          data-test="patient-conflict-stay"
          @click="store.resolvePatientChange('stay')"
        >
          Stay on {{ pending.from }}
        </button>
        <button
          v-if="hasStaleTarget"
          type="button"
          class="btn btn-outline-danger"
          data-test="patient-conflict-fresh"
          @click="store.resolvePatientChange('fresh')"
        >
          Start fresh with {{ pending.to }}
        </button>
      </div>
    </div>
  </div>
</template>
