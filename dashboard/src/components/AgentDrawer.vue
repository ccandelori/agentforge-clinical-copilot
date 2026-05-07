<script setup lang="ts">
import { computed, ref } from 'vue'

import AgentChatPane from '@/components/agent/AgentChatPane.vue'
import CitationsPane from '@/components/agent/CitationsPane.vue'
import HistoryPane from '@/components/agent/HistoryPane.vue'
import PatientContextConflictOverlay from '@/components/PatientContextConflictOverlay.vue'
import {
  useAgentDrawer,
  type AgentMode,
  type AgentTab,
} from '@/stores/agentDrawer'

// AgentForge drawer shell (T38.10 + T38.11 + UX polish).
//
// Lives at the App.vue level so it persists across route changes.
// Reads/writes through the `useAgentDrawer` Pinia store; this component
// stays focused on layout, the mode-tab strip, and the new tab strip
// (Chat / Citations / History).
//
// The drawer has TWO orthogonal axes:
//   * Mode  — Chart / Intake / Research (which agent surface). Owned
//             by `store.mode`. Determines the active scope.
//   * Tab   — Chat / Citations / History (which view of the active
//             scope). Owned by `store.activeTab`. Independent of mode.
//
// Chart-mode messages are sent through the auth bridge described in
// docs/adr/0001-dashboard-auth-bridging.md: dashboard cookie →
// sidecar session → minted internal JWT → AuthGateway → Orchestrator.
// The chat pane (`AgentChatPane.vue`) owns the actual `/api/agent/turn`
// call; this shell just hosts the panes.

const store = useAgentDrawer()

interface ModeTabDef {
  readonly mode: AgentMode
  readonly label: string
  readonly testId: string
}

interface MainTabDef {
  readonly tab: AgentTab
  readonly label: string
  readonly testId: string
}

const modeTabs: readonly ModeTabDef[] = [
  { mode: 'chart', label: 'Chart', testId: 'agent-tab-chart' },
  { mode: 'intake', label: 'Intake', testId: 'agent-tab-intake' },
  { mode: 'research', label: 'Research', testId: 'agent-tab-research' },
]

const mainTabs: readonly MainTabDef[] = [
  { tab: 'chat', label: 'Chat', testId: 'agent-maintab-chat' },
  { tab: 'citations', label: 'Citations', testId: 'agent-maintab-citations' },
  { tab: 'history', label: 'History', testId: 'agent-maintab-history' },
]

const highlightedCitationId = ref<string | null>(null)

function modeDisabled(mode: AgentMode): boolean {
  if (mode === 'chart') return !store.canChart
  if (mode === 'intake') return !store.canIntake
  return false
}

function selectMode(mode: AgentMode): void {
  if (modeDisabled(mode)) return
  store.setMode(mode)
}

function selectTab(tab: AgentTab): void {
  store.setActiveTab(tab)
  if (tab !== 'citations') {
    highlightedCitationId.value = null
  }
}

function onCitationClickFromChat(id: string): void {
  highlightedCitationId.value = id
  store.setActiveTab('citations')
}

function onSelectConversation(id: string): void {
  store.selectConversation(id)
}

function onNewConversation(): void {
  store.newConversation()
}

const citationsCount = computed<number>(() => store.currentCitations.length)
const historyCount = computed<number>(() => store.conversationHistory.length)
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
    v-if="store.open"
    class="agent-drawer shadow-lg d-flex flex-column"
    role="dialog"
    aria-label="AgentForge drawer"
    data-test="agent-drawer"
  >
    <header
      class="agent-drawer__header border-bottom px-3 py-2 d-flex flex-column gap-2"
    >
      <div class="d-flex align-items-center gap-2">
        <div
          class="agent-drawer__brand-icon d-flex align-items-center justify-content-center rounded-2 flex-shrink-0"
          aria-hidden="true"
        >
          <i class="bi bi-stars"></i>
        </div>
        <div class="d-flex flex-column lh-sm flex-grow-1 min-w-0">
          <span class="fw-semibold small">AgentForge</span>
          <span class="text-body-secondary" style="font-size: 0.7rem;">
            Clinical Co-Pilot
          </span>
        </div>
        <button
          type="button"
          class="btn btn-link btn-sm text-body-secondary p-1"
          aria-label="New conversation"
          title="New conversation"
          data-test="agent-new-conversation"
          @click="onNewConversation"
        >
          <i class="bi bi-plus-lg" aria-hidden="true"></i>
        </button>
        <button
          type="button"
          class="btn btn-link btn-sm text-body-secondary p-1"
          data-test="agent-drawer-close"
          aria-label="Close drawer"
          @click="store.close"
        >
          <i class="bi bi-x-lg" aria-hidden="true"></i>
        </button>
      </div>

      <div
        class="btn-group btn-group-sm w-100"
        role="tablist"
        aria-label="Agent mode"
      >
        <button
          v-for="t in modeTabs"
          :key="t.mode"
          type="button"
          class="btn"
          :class="store.mode === t.mode ? 'btn-primary' : 'btn-outline-secondary'"
          role="tab"
          :data-test="t.testId"
          :disabled="modeDisabled(t.mode)"
          :aria-selected="store.mode === t.mode ? 'true' : 'false'"
          @click="selectMode(t.mode)"
        >
          {{ t.label }}
        </button>
      </div>
    </header>

    <nav
      class="agent-drawer__tabs border-bottom d-flex"
      role="tablist"
      aria-label="Drawer view"
    >
      <button
        v-for="t in mainTabs"
        :key="t.tab"
        type="button"
        class="agent-drawer__tab flex-grow-1 btn btn-link btn-sm rounded-0 px-3 py-2 d-inline-flex align-items-center justify-content-center gap-1"
        :class="store.activeTab === t.tab ? 'agent-drawer__tab--active' : ''"
        role="tab"
        :data-test="t.testId"
        :aria-selected="store.activeTab === t.tab ? 'true' : 'false'"
        @click="selectTab(t.tab)"
      >
        <span>{{ t.label }}</span>
        <span
          v-if="t.tab === 'citations' && citationsCount > 0"
          class="badge text-bg-secondary"
          style="font-size: 0.625rem;"
        >
          {{ citationsCount }}
        </span>
        <span
          v-if="t.tab === 'history' && historyCount > 0"
          class="badge text-bg-secondary"
          style="font-size: 0.625rem;"
        >
          {{ historyCount }}
        </span>
      </button>
    </nav>

    <div
      class="agent-drawer__body flex-grow-1 position-relative overflow-hidden"
    >
      <PatientContextConflictOverlay />

      <!-- Chat pane stays mounted (v-show) so existing data-test ids
           (agent-input, agent-send, agent-message-list) remain in the
           DOM regardless of which tab is active — and so the composer's
           textarea height tracking survives tab switches. -->
      <div
        v-show="store.activeTab === 'chat'"
        class="h-100"
        role="tabpanel"
        aria-label="Chat"
      >
        <AgentChatPane @citation-click="onCitationClickFromChat" />
      </div>
      <div
        v-if="store.activeTab === 'citations'"
        class="h-100"
        role="tabpanel"
        aria-label="Citations"
      >
        <CitationsPane :highlighted-id="highlightedCitationId" />
      </div>
      <div
        v-if="store.activeTab === 'history'"
        class="h-100"
        role="tabpanel"
        aria-label="History"
      >
        <HistoryPane @select="onSelectConversation" />
      </div>
    </div>
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
  background-color: var(--surface);
  color: var(--ink);
  border-left: 1px solid var(--line);
}

.agent-drawer__brand-icon {
  width: 2rem;
  height: 2rem;
  background-color: var(--bs-primary);
  color: #fff;
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

.agent-drawer__tab {
  color: var(--ink-muted);
  text-decoration: none;
  border-bottom: 2px solid transparent;
  font-weight: 500;
}

.agent-drawer__tab:hover,
.agent-drawer__tab:focus-visible {
  color: var(--ink);
  background-color: var(--surface-2);
}

.agent-drawer__tab--active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.agent-drawer__tab--active:hover {
  color: var(--accent-hover);
}
</style>
