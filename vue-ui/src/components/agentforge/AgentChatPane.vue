<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { useAgentForgeStore, type ChatMessage } from '@/stores/agentforge'

import AgentMessage from './AgentMessage.vue'

const emit = defineEmits<{
  (e: 'citation-click', id: string): void
}>()

const store = useAgentForgeStore()

const draft = ref<string>('')
const composerEl = ref<HTMLTextAreaElement | null>(null)
const scrollEl = ref<HTMLDivElement | null>(null)
const autoScrollPaused = ref<boolean>(false)

const SUGGESTION_CHIPS: readonly string[] = [
  'Summarize last visit',
  'Suggest differential',
  'Draft note from my dictation',
  'Show abnormal labs',
]

const messages = computed<readonly ChatMessage[]>(() => store.messages)
const showEmptyState = computed<boolean>(() => {
  return messages.value.length === 0 && store.pendingAssistantText === null
})

// Synthetic message that renders the in-flight assistant reply during streaming.
const pendingMessage = computed<ChatMessage | null>(() => {
  const text = store.pendingAssistantText
  if (text === null) return null
  return {
    id: 'pending-assistant',
    role: 'assistant',
    text,
    createdAt: new Date().toISOString(),
  }
})

function scrollToBottom(force = false): void {
  const el = scrollEl.value
  if (el === null) return
  if (!force && autoScrollPaused.value) return
  el.scrollTop = el.scrollHeight
}

function onScroll(): void {
  const el = scrollEl.value
  if (el === null) return
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  // 24px slack so any rounding doesn't permanently latch the pause.
  autoScrollPaused.value = distanceFromBottom > 24
}

watch(
  () => messages.value.length,
  () => {
    autoScrollPaused.value = false
    void nextTick(() => scrollToBottom(true))
  },
)

watch(
  () => store.pendingAssistantText,
  () => {
    void nextTick(() => scrollToBottom())
  },
)

onMounted(() => {
  void nextTick(() => scrollToBottom(true))
  composerEl.value?.focus()
})

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (text.length === 0) return
  if (store.isStreaming) return
  draft.value = ''
  // Reset textarea height after clearing the value.
  if (composerEl.value !== null) {
    composerEl.value.style.height = 'auto'
  }
  await store.sendMessage(text)
}

function onComposerKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault()
    void send()
  }
}

function onComposerInput(): void {
  const el = composerEl.value
  if (el === null) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function pickSuggestion(s: string): void {
  if (store.isStreaming) return
  draft.value = s
  void nextTick(() => {
    composerEl.value?.focus()
    onComposerInput()
  })
}

function onCitationClick(id: string): void {
  emit('citation-click', id)
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div
      ref="scrollEl"
      class="min-h-0 flex-1 overflow-y-auto px-4 py-4"
      @scroll="onScroll"
    >
      <div v-if="showEmptyState" class="flex h-full flex-col items-center justify-center gap-6 py-8 text-center">
        <div
          class="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-900/30 dark:text-primary-300"
          aria-hidden="true"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-6 w-6">
            <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
            <circle cx="12" cy="12" r="4" />
          </svg>
        </div>
        <div class="space-y-1">
          <h2 class="text-base font-semibold text-ink">
            How can I help with this patient/encounter today?
          </h2>
          <p class="text-xs text-ink-muted">
            AgentForge will cite the chart sources behind every answer.
          </p>
        </div>
        <div class="flex w-full max-w-sm flex-col gap-2">
          <button
            v-for="s in SUGGESTION_CHIPS"
            :key="s"
            type="button"
            class="rounded-xl border border-line bg-surface px-3 py-2 text-left text-sm text-ink transition-colors hover:border-primary-300 hover:bg-primary-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-primary-900/20"
            @click="pickSuggestion(s)"
          >
            {{ s }}
          </button>
        </div>
      </div>

      <div v-else class="flex flex-col gap-4">
        <AgentMessage
          v-for="m in messages"
          :key="m.id"
          :message="m"
          @citation-click="onCitationClick"
        />
        <AgentMessage
          v-if="pendingMessage"
          :key="pendingMessage.id"
          :message="pendingMessage"
          :pending="true"
        />
      </div>
    </div>

    <div class="shrink-0 border-t border-line bg-surface p-3">
      <div
        class="flex items-end gap-2 rounded-xl border border-line bg-surface-2 px-2 py-1.5 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500/30"
      >
        <button
          type="button"
          class="shrink-0 rounded-md p-1.5 text-ink-muted hover:bg-surface hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          aria-label="Attach file"
          title="Attach file (coming soon)"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
            <path d="M21 12.5 12.5 21a5 5 0 0 1-7-7L14 5.5a3.5 3.5 0 0 1 5 5L10.5 19a2 2 0 1 1-3-3L15 8.5" />
          </svg>
        </button>

        <textarea
          ref="composerEl"
          v-model="draft"
          rows="1"
          placeholder="Ask the co-pilot…"
          class="min-h-[28px] flex-1 resize-none bg-transparent px-1 py-1 text-sm text-ink placeholder:text-ink-muted focus:outline-none"
          :disabled="store.isStreaming"
          @keydown="onComposerKeydown"
          @input="onComposerInput"
        />

        <button
          type="button"
          class="shrink-0 rounded-md bg-primary-600 p-1.5 text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="draft.trim().length === 0 || store.isStreaming"
          aria-label="Send message"
          @click="send"
        >
          <svg
            v-if="!store.isStreaming"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="h-4 w-4"
            aria-hidden="true"
          >
            <path d="m4 12 16-8-6 16-2-7-8-1z" />
          </svg>
          <span
            v-else
            class="block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
            aria-hidden="true"
          />
        </button>
      </div>
      <p class="mt-1.5 px-1 text-[11px] text-ink-muted">
        Enter to send · Shift + Enter for new line
      </p>
    </div>
  </div>
</template>
