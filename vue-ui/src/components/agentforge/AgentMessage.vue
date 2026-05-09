<script setup lang="ts">
import { computed, ref } from 'vue'

import type {
  ChatMessage,
  Citation,
  ExtractionResult,
} from '@/stores/agentforge'

import CitationPill from './CitationPill.vue'
import ExtractionPanel from './ExtractionPanel.vue'
import LabPanel from './LabPanel.vue'

interface Props {
  message: ChatMessage
  pending?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  pending: false,
})

const emit = defineEmits<{
  (e: 'citation-click', id: string): void
}>()

const isUser = computed<boolean>(() => props.message.role === 'user')
const isError = computed<boolean>(() => props.message.error === true)

const citations = computed<readonly Citation[]>(() => {
  return props.message.citations ?? []
})

const extraction = computed<ExtractionResult | null>(() => {
  return props.message.extraction ?? null
})

const bubbleClass = computed<string>(() => {
  if (isUser.value) return 'bg-primary-600 text-white'
  if (isError.value) {
    return 'border border-danger-300 bg-danger-50 text-danger-700 dark:border-danger-700/60 dark:bg-danger-900/30 dark:text-danger-300'
  }
  return 'border border-line bg-surface text-ink'
})

const copied = ref<boolean>(false)
let copyResetTimer: ReturnType<typeof setTimeout> | null = null

async function onCopy(): Promise<void> {
  if (typeof navigator === 'undefined' || !navigator.clipboard) return
  try {
    await navigator.clipboard.writeText(props.message.text)
    copied.value = true
    if (copyResetTimer !== null) clearTimeout(copyResetTimer)
    copyResetTimer = setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    // Clipboard not available — silently ignore.
  }
}

function onCitationClick(id: string): void {
  emit('citation-click', id)
}
</script>

<template>
  <div
    class="group flex w-full"
    :class="isUser ? 'justify-end' : 'justify-start'"
  >
    <div
      class="flex max-w-[88%] flex-col gap-2"
      :class="isUser ? 'items-end' : 'items-start'"
    >
      <div
        class="rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-card"
        :class="bubbleClass"
      >
        <span class="whitespace-pre-wrap break-words">{{ message.text }}</span>
        <span
          v-if="pending"
          class="ml-1 inline-block h-2 w-2 animate-pulse rounded-full bg-current align-middle"
          aria-hidden="true"
        />
      </div>

      <div
        v-if="!isUser && citations.length > 0"
        class="flex flex-wrap gap-1.5"
      >
        <CitationPill
          v-for="(c, idx) in citations"
          :key="c.id"
          :citation="c"
          :index="idx"
          @select="onCitationClick"
        />
      </div>

      <div v-if="!isUser && extraction !== null" class="w-full max-w-md">
        <!--
          Discriminated dispatch (P1.2): the ``kind`` tag is set at the
          parser boundary in ``useAgentTurn`` so we mount the panel that
          knows the matching shape. Lab and intake snapshots have
          disjoint schemas; rendering the wrong panel would silently
          discard the other side's structured rows.
        -->
        <LabPanel
          v-if="extraction.kind === 'lab'"
          :extraction="extraction"
        />
        <ExtractionPanel
          v-else-if="extraction.kind === 'intake'"
          :extraction="extraction"
        />
      </div>

      <div
        v-if="!isUser && !pending"
        class="flex items-center gap-2 text-[11px] text-ink-muted opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"
      >
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          :aria-label="copied ? 'Copied' : 'Copy message'"
          @click="onCopy"
        >
          <svg
            v-if="!copied"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="h-3 w-3"
            aria-hidden="true"
          >
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V5a2 2 0 0 1 2-2h10" />
          </svg>
          <svg
            v-else
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="h-3 w-3 text-success-600"
            aria-hidden="true"
          >
            <path d="m5 12 5 5L20 7" />
          </svg>
          <span>{{ copied ? 'Copied' : 'Copy' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
