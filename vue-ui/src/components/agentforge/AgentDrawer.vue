<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useAgentForgeStore } from '@/stores/agentforge'
import { useUiStore } from '@/stores/ui'

import AgentChatPane from './AgentChatPane.vue'
import CitationsPane from './CitationsPane.vue'
import HistoryPane from './HistoryPane.vue'

type Tab = 'chat' | 'citations' | 'history'

interface TabDef {
  readonly id: Tab
  readonly label: string
}

const ui = useUiStore()
const store = useAgentForgeStore()

const activeTab = ref<Tab>('chat')
const highlightedCitationId = ref<string | null>(null)

const TABS: readonly TabDef[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'citations', label: 'Citations' },
  { id: 'history', label: 'History' },
]

function close(): void {
  ui.closeAgentDrawer()
}

function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && ui.agentDrawerOpen) {
    ev.stopPropagation()
    close()
  }
}

function onCitationClickFromChat(id: string): void {
  highlightedCitationId.value = id
  activeTab.value = 'citations'
}

function onSelectConversation(id: string): void {
  store.selectConversation(id)
  activeTab.value = 'chat'
}

function onNewConversation(): void {
  store.newConversation()
  activeTab.value = 'chat'
}

function setTab(tab: Tab): void {
  activeTab.value = tab
  if (tab !== 'citations') {
    highlightedCitationId.value = null
  }
}

watch(
  () => ui.agentDrawerOpen,
  (open) => {
    if (open) {
      store.hydrate()
    }
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="#drawer-root">
    <Transition
      enter-active-class="transition-transform duration-300 ease-out"
      enter-from-class="translate-x-full"
      enter-to-class="translate-x-0"
      leave-active-class="transition-transform duration-200 ease-in"
      leave-from-class="translate-x-0"
      leave-to-class="translate-x-full"
    >
      <aside
        v-if="ui.agentDrawerOpen"
        class="fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l border-line bg-surface shadow-card-lg sm:w-[480px]"
        role="complementary"
        aria-label="AgentForge Clinical Co-Pilot"
      >
        <!-- Header -->
        <header class="flex h-14 shrink-0 items-center gap-2 border-b border-line px-3">
          <div
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-600 text-white"
            aria-hidden="true"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
              <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
              <circle cx="12" cy="12" r="4" />
            </svg>
          </div>
          <div class="flex min-w-0 flex-col">
            <span class="text-sm font-semibold tracking-tight text-ink">AgentForge</span>
            <span class="text-[11px] text-ink-muted">Clinical Co-Pilot</span>
          </div>

          <div class="ml-auto flex items-center gap-1">
            <button
              type="button"
              class="rounded-md p-2 text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              aria-label="New conversation"
              title="New conversation"
              @click="onNewConversation"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
            <button
              type="button"
              class="rounded-md p-2 text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              aria-label="Close co-pilot"
              @click="close"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
                <path d="m6 6 12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
        </header>

        <!-- Tabs -->
        <nav class="flex shrink-0 items-stretch border-b border-line bg-surface" role="tablist">
          <button
            v-for="tab in TABS"
            :key="tab.id"
            type="button"
            role="tab"
            :aria-selected="activeTab === tab.id"
            :tabindex="activeTab === tab.id ? 0 : -1"
            class="flex-1 border-b-2 px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
            :class="
              activeTab === tab.id
                ? 'border-primary-600 text-primary-700 dark:text-primary-300'
                : 'border-transparent text-ink-muted hover:bg-surface-2 hover:text-ink'
            "
            @click="setTab(tab.id)"
          >
            {{ tab.label }}
          </button>
        </nav>

        <!-- Tab body -->
        <div class="min-h-0 flex-1">
          <AgentChatPane
            v-show="activeTab === 'chat'"
            class="h-full"
            @citation-click="onCitationClickFromChat"
          />
          <CitationsPane
            v-if="activeTab === 'citations'"
            class="h-full"
            :highlighted-id="highlightedCitationId"
          />
          <HistoryPane
            v-if="activeTab === 'history'"
            class="h-full"
            @select="onSelectConversation"
          />
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>
