<script setup lang="ts">
import { computed } from 'vue'

import { useAgentForgeStore, type ChatMessage, type Conversation } from '@/stores/agentforge'

const emit = defineEmits<{
  (e: 'select', id: string): void
}>()

const store = useAgentForgeStore()

interface HistoryItem {
  readonly id: string
  readonly title: string
  readonly preview: string
  readonly createdAt: string
  readonly relativeLabel: string
  readonly active: boolean
}

function firstUserPreview(c: Conversation): string {
  const firstUser: ChatMessage | undefined = c.messages.find(
    (m) => m.role === 'user',
  )
  if (firstUser !== undefined) {
    const t = firstUser.text.trim()
    return t.length > 96 ? `${t.slice(0, 93)}...` : t
  }
  return 'No messages yet.'
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

const items = computed<readonly HistoryItem[]>(() => {
  return store.sortedConversations.map((c) => ({
    id: c.id,
    title: c.title,
    preview: firstUserPreview(c),
    createdAt: c.createdAt,
    relativeLabel: relativeLabel(c.createdAt),
    active: c.id === store.activeConversationId,
  }))
})

function onSelect(id: string): void {
  emit('select', id)
}
</script>

<template>
  <div class="h-full min-h-0 overflow-y-auto px-2 py-3">
    <ul v-if="items.length > 0" class="flex flex-col gap-1">
      <li v-for="it in items" :key="it.id">
        <button
          type="button"
          class="flex w-full flex-col gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          :class="
            it.active
              ? 'border-primary-300 bg-primary-50 dark:border-primary-700/60 dark:bg-primary-900/20'
              : 'border-transparent hover:border-line hover:bg-surface-2'
          "
          @click="onSelect(it.id)"
        >
          <div class="flex items-baseline justify-between gap-2">
            <span class="truncate text-sm font-semibold text-ink">{{ it.title }}</span>
            <span class="shrink-0 text-[11px] text-ink-muted">{{ it.relativeLabel }}</span>
          </div>
          <p class="line-clamp-2 text-xs text-ink-muted">{{ it.preview }}</p>
        </button>
      </li>
    </ul>
    <div v-else class="flex h-full flex-col items-center justify-center gap-2 text-center">
      <h3 class="text-sm font-semibold text-ink">No conversations yet</h3>
      <p class="max-w-sm text-xs text-ink-muted">
        Start a chat in the Chat tab and your past sessions will appear here.
      </p>
    </div>
  </div>
</template>
