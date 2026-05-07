<script setup lang="ts">
import { computed } from 'vue'

import {
  useAgentDrawer,
  type ConversationSnapshot,
} from '@/stores/agentDrawer'

// HistoryPane — lists past archived conversations newest-first with a
// relative timestamp + first-user-message preview. Clicking an item
// emits `select(snapshotId)`; the drawer wires that to
// `store.selectConversation(id)` which restores the snapshot into its
// scope and switches back to the Chat tab.
//
// Lifted from `vue-ui/src/components/agentforge/HistoryPane.vue`,
// rewired against `useAgentDrawer.conversationHistory` (sessionStorage
// snapshots, not the localStorage store the vue-ui sibling used) and
// translated from Tailwind to Bootstrap 5.

const emit = defineEmits<{
  (e: 'select', id: string): void
}>()

const store = useAgentDrawer()

interface HistoryItem {
  readonly id: string
  readonly title: string
  readonly preview: string
  readonly relativeLabel: string
  readonly modeLabel: string
}

function firstUserPreview(snap: ConversationSnapshot): string {
  const firstUser = snap.messages.find((m) => m.role === 'user')
  if (firstUser !== undefined) {
    const t = firstUser.text.trim()
    return t.length > 96 ? `${t.slice(0, 93)}...` : t
  }
  return 'No user prompt recorded.'
}

function snapshotTitle(snap: ConversationSnapshot): string {
  const firstUser = snap.messages.find((m) => m.role === 'user')
  if (firstUser !== undefined) {
    const t = firstUser.text.trim()
    return t.length > 48 ? `${t.slice(0, 45)}...` : t
  }
  return 'Empty conversation'
}

function relativeLabel(iso: string): string {
  const then = new Date(iso).getTime()
  const diffMs = Date.now() - then
  if (Number.isNaN(diffMs)) return ''
  const sec = Math.round(diffMs / 1000)
  if (sec < 60) return 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.round(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.round(hr / 24)
  if (day < 7) return `${day}d ago`
  const wk = Math.round(day / 7)
  if (wk < 5) return `${wk}w ago`
  return new Date(iso).toLocaleDateString()
}

function modeLabel(snap: ConversationSnapshot): string {
  switch (snap.mode) {
    case 'chart':
      return 'Chart'
    case 'intake':
      return 'Intake'
    case 'research':
      return 'Research'
  }
}

const items = computed<readonly HistoryItem[]>(() => {
  const sorted = [...store.conversationHistory].sort((a, b) =>
    b.startedAt.localeCompare(a.startedAt),
  )
  return sorted.map<HistoryItem>((snap) => ({
    id: snap.id,
    title: snapshotTitle(snap),
    preview: firstUserPreview(snap),
    relativeLabel: relativeLabel(snap.startedAt),
    modeLabel: modeLabel(snap),
  }))
})

function onSelect(id: string): void {
  emit('select', id)
}
</script>

<template>
  <div
    class="agent-history-pane h-100 overflow-auto px-2 py-3"
    data-test="agent-history-pane"
  >
    <ul
      v-if="items.length > 0"
      class="list-unstyled mb-0 d-flex flex-column gap-1"
    >
      <li v-for="it in items" :key="it.id">
        <button
          type="button"
          class="agent-history-item w-100 text-start rounded p-3 d-flex flex-column gap-1"
          data-test="agent-history-item"
          @click="onSelect(it.id)"
        >
          <div class="d-flex justify-content-between align-items-baseline gap-2">
            <span class="fw-semibold small text-truncate">{{ it.title }}</span>
            <span
              class="text-body-secondary flex-shrink-0"
              style="font-size: 0.7rem;"
            >
              {{ it.relativeLabel }}
            </span>
          </div>
          <p
            class="agent-history-item__preview mb-0 text-body-secondary"
            style="font-size: 0.75rem;"
          >
            {{ it.preview }}
          </p>
          <span
            class="badge bg-secondary-subtle text-secondary-emphasis align-self-start"
            style="font-size: 0.625rem;"
          >
            {{ it.modeLabel }}
          </span>
        </button>
      </li>
    </ul>
    <div
      v-else
      class="d-flex flex-column align-items-center justify-content-center text-center h-100 gap-2"
      data-test="agent-history-empty"
    >
      <h3 class="h6 mb-0">No conversations yet</h3>
      <p class="small mb-0 text-body-secondary" style="max-width: 22rem;">
        Start a chat in the Chat tab. Click "New conversation" in the header
        to archive the current chat into history.
      </p>
    </div>
  </div>
</template>

<style scoped>
.agent-history-item {
  background-color: transparent;
  border: 1px solid transparent;
  color: var(--ink);
  transition: background-color 120ms ease, border-color 120ms ease;
}

.agent-history-item:hover,
.agent-history-item:focus-visible {
  background-color: var(--surface-2);
  border-color: var(--line);
}

.agent-history-item__preview {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
