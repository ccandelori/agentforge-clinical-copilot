<script setup lang="ts">
import { ref } from 'vue'

import PatientContextConflictOverlay from '@/components/PatientContextConflictOverlay.vue'
import { useAgentDrawer, type AgentMode } from '@/stores/agentDrawer'

// AgentForge drawer shell (T38.10).
//
// Lives at the App.vue level so it persists across route changes.
// Reads/writes through the `useAgentDrawer` Pinia store; this component
// stays focused on layout, animation, and mode-tab affordances.
//
// The chat I/O is intentionally minimal — addUserTurn echoes into the
// active scope; bridging to the sidecar /turn endpoint lands with the
// follow-up subtasks (T38.11+). Disabling the input while a pending
// patient-change is staged is the only conflict-related responsibility
// here; the overlay component handles its own visibility + buttons.

const store = useAgentDrawer()
const draft = ref<string>('')

const tabs: { mode: AgentMode; label: string; testId: string }[] = [
  { mode: 'chart', label: 'Chart', testId: 'agent-tab-chart' },
  { mode: 'intake', label: 'Intake', testId: 'agent-tab-intake' },
  { mode: 'research', label: 'Research', testId: 'agent-tab-research' },
]

function tabDisabled(mode: AgentMode): boolean {
  if (mode === 'chart') return !store.canChart
  if (mode === 'intake') return !store.canIntake
  return false
}

function selectTab(mode: AgentMode): void {
  if (tabDisabled(mode)) return
  store.setMode(mode)
}

function send(): void {
  if (store.pendingPatientChange !== null) return
  const text = draft.value.trim()
  if (text === '') return
  store.addUserTurn(text)
  draft.value = ''
}
</script>

<template>
  <button
    v-if="!store.open"
    type="button"
    class="agent-drawer__toggle btn btn-primary shadow"
    data-test="agent-drawer-toggle"
    aria-label="Open AgentForge drawer"
    @click="store.toggle"
  >
    <i class="bi bi-stars me-1" aria-hidden="true"></i>
    Agent
  </button>

  <aside
    v-show="store.open"
    class="agent-drawer shadow-lg bg-white d-flex flex-column"
    role="dialog"
    aria-label="AgentForge drawer"
    data-test="agent-drawer"
  >
    <header
      class="agent-drawer__header border-bottom px-3 py-2 d-flex align-items-center"
    >
      <div
        class="btn-group btn-group-sm flex-grow-1"
        role="tablist"
        aria-label="Agent mode"
      >
        <button
          v-for="tab in tabs"
          :key="tab.mode"
          type="button"
          class="btn"
          :class="store.mode === tab.mode ? 'btn-primary' : 'btn-outline-secondary'"
          role="tab"
          :data-test="tab.testId"
          :disabled="tabDisabled(tab.mode)"
          :aria-selected="store.mode === tab.mode ? 'true' : 'false'"
          @click="selectTab(tab.mode)"
        >
          {{ tab.label }}
        </button>
      </div>
      <button
        type="button"
        class="btn btn-link text-muted ms-2 p-1"
        data-test="agent-drawer-close"
        aria-label="Close drawer"
        @click="store.close"
      >
        <i class="bi bi-x-lg" aria-hidden="true"></i>
      </button>
    </header>

    <div
      class="agent-drawer__body flex-grow-1 overflow-auto position-relative px-3 py-3"
    >
      <PatientContextConflictOverlay />

      <ul
        class="list-unstyled mb-0 d-flex flex-column gap-2"
        data-test="agent-message-list"
      >
        <li
          v-for="(msg, idx) in store.currentMessages"
          :key="idx"
          :class="msg.role === 'user' ? 'text-end' : 'text-start'"
        >
          <span
            class="d-inline-block px-3 py-2 rounded"
            :class="
              msg.role === 'user'
                ? 'bg-primary text-white'
                : 'bg-light text-body border'
            "
          >
            {{ msg.text }}
          </span>
        </li>
      </ul>
      <p
        v-if="store.currentMessages.length === 0"
        class="text-muted small mb-0"
      >
        Start a conversation in
        <strong>{{ store.mode }}</strong> mode.
      </p>
    </div>

    <footer class="agent-drawer__footer border-top p-2">
      <div class="input-group input-group-sm">
        <input
          v-model="draft"
          type="text"
          class="form-control"
          placeholder="Ask AgentForge…"
          data-test="agent-input"
          :disabled="store.pendingPatientChange !== null"
          @keyup.enter="send"
        />
        <button
          type="button"
          class="btn btn-primary"
          data-test="agent-send"
          :disabled="store.pendingPatientChange !== null"
          @click="send"
        >
          Send
        </button>
      </div>
    </footer>
  </aside>
</template>

<style scoped>
.agent-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 480px;
  max-width: 100vw;
  z-index: 1040;
}

.agent-drawer__toggle {
  position: fixed;
  top: 50%;
  right: 0;
  transform: translateY(-50%) rotate(-90deg) translateY(-100%);
  transform-origin: top right;
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
  z-index: 1030;
}
</style>
