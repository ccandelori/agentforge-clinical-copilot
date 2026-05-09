<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { inferDocType } from '@/composables/inferDocType'
import { useDocumentUpload } from '@/composables/useDocumentUpload'
import { useAgentForgeStore, type ChatMessage } from '@/stores/agentforge'

import AgentMessage from './AgentMessage.vue'

const emit = defineEmits<{
  (e: 'citation-click', id: string): void
}>()

const store = useAgentForgeStore()
const route = useRoute()
const { uploadDocument, isUploading } = useDocumentUpload()

const draft = ref<string>('')
const composerEl = ref<HTMLTextAreaElement | null>(null)
const scrollEl = ref<HTMLDivElement | null>(null)
const fileInputEl = ref<HTMLInputElement | null>(null)
const uploadError = ref<string | null>(null)
const autoScrollPaused = ref<boolean>(false)

/**
 * Patient UUID for the upload route. Mirrors the store's ``currentPatientUuid``
 * — the upload route requires a patient context so the BFF can scope the
 * attachment to the right chart. When we're not on a patient page the
 * attach button is disabled (the file picker never opens).
 */
const patientUuid = computed<string | null>(() => {
  if (route.name !== 'patient-dashboard') return null
  const id = route.params['id']
  if (typeof id !== 'string' || id.length === 0) return null
  return id
})

const canAttach = computed<boolean>(
  () => patientUuid.value !== null && !store.isSending && !isUploading.value,
)

const pendingAttachment = computed(() => store.pendingAttachment)

const SUGGESTION_CHIPS: readonly string[] = [
  'Summarize last visit',
  'Suggest differential',
  'Draft note from my dictation',
  'Show abnormal labs',
]

const messages = computed<readonly ChatMessage[]>(() => store.messages)
const showEmptyState = computed<boolean>(() => {
  return messages.value.length === 0 && !store.isSending
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
  () => store.isSending,
  (sending) => {
    if (sending) {
      void nextTick(() => scrollToBottom())
    }
  },
)

onMounted(() => {
  void nextTick(() => scrollToBottom(true))
  composerEl.value?.focus()
})

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (text.length === 0) return
  if (store.isSending) return
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
  if (store.isSending) return
  draft.value = s
  void nextTick(() => {
    composerEl.value?.focus()
    onComposerInput()
  })
}

function onCitationClick(id: string): void {
  emit('citation-click', id)
}

function onAttachClick(): void {
  if (!canAttach.value) return
  uploadError.value = null
  fileInputEl.value?.click()
}

async function onFileSelected(ev: Event): Promise<void> {
  const target = ev.target as HTMLInputElement | null
  if (target === null) return
  const file = target.files?.[0] ?? null
  // Reset the input synchronously so the same file picked twice in a
  // row still fires another ``change`` event.
  target.value = ''
  if (file === null) return
  const uuid = patientUuid.value
  if (uuid === null) {
    uploadError.value = 'Open a patient chart before attaching a file.'
    return
  }
  try {
    const docType = inferDocType(file.name)
    const { document_id } = await uploadDocument(file, uuid, docType)
    // The same ``docType`` rides one chat turn as ``doc_type`` so the
    // BFF graph dispatches the extractor on the right schema (lab vs
    // intake). Without this, lab PDFs silently default to intake.
    store.setPendingAttachment({
      documentId: document_id,
      filename: file.name,
      docType,
    })
    uploadError.value = null
  } catch (caught) {
    uploadError.value
      = caught instanceof Error && caught.message.length > 0
        ? caught.message
        : 'Upload failed. Try again.'
  }
}

function removePendingAttachment(): void {
  store.clearPendingAttachment()
}

function onGuidelineToggle(): void {
  if (store.isSending) return
  store.toggleGuidelineMode()
  // Keep focus on the composer after toggling so the clinician can
  // type their question without an extra click.
  void nextTick(() => composerEl.value?.focus())
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
        <div
          v-if="store.isSending"
          class="flex justify-start"
          aria-live="polite"
          data-test="agent-thinking"
        >
          <div
            class="flex items-center gap-2 rounded-2xl border border-line bg-surface px-4 py-2.5 text-sm text-ink-muted shadow-card"
          >
            <span class="flex gap-1" aria-hidden="true">
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:0ms]" />
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:150ms]" />
              <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:300ms]" />
            </span>
            <span>Thinking…</span>
          </div>
        </div>
      </div>
    </div>

    <div class="shrink-0 border-t border-line bg-surface p-3">
      <!-- Pending attachment chip + upload error live above the
        composer so they don't get lost in the textarea grow zone. -->
      <div
        v-if="pendingAttachment !== null"
        class="mb-2 flex items-center gap-2 rounded-lg border border-primary-300 bg-primary-50 px-2 py-1 text-xs text-primary-800 dark:border-primary-700 dark:bg-primary-900/30 dark:text-primary-200"
        data-test="pending-attachment"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5 shrink-0" aria-hidden="true">
          <path d="M21 12.5 12.5 21a5 5 0 0 1-7-7L14 5.5a3.5 3.5 0 0 1 5 5L10.5 19a2 2 0 1 1-3-3L15 8.5" />
        </svg>
        <span class="truncate">{{ pendingAttachment.filename }}</span>
        <button
          type="button"
          class="ml-auto rounded p-0.5 text-current hover:bg-primary-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:hover:bg-primary-800"
          aria-label="Remove attachment"
          @click="removePendingAttachment"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3 w-3" aria-hidden="true">
            <path d="M6 6l12 12M6 18 18 6" />
          </svg>
        </button>
      </div>
      <div
        v-if="uploadError !== null"
        class="mb-2 rounded-lg border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
        role="alert"
        data-test="upload-error"
      >
        {{ uploadError }}
      </div>

      <div
        class="flex items-end gap-2 rounded-xl border border-line bg-surface-2 px-2 py-1.5 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500/30"
      >
        <input
          ref="fileInputEl"
          type="file"
          accept=".pdf,application/pdf"
          class="sr-only"
          aria-hidden="true"
          tabindex="-1"
          data-test="file-input"
          @change="onFileSelected"
        >
        <button
          type="button"
          class="shrink-0 rounded-md p-1.5 text-ink-muted transition-colors hover:bg-surface hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          :aria-label="patientUuid === null ? 'Attach file (open a patient chart first)' : 'Attach file'"
          :title="patientUuid === null ? 'Open a patient chart to attach a file' : 'Attach a PDF'"
          :disabled="!canAttach"
          data-test="attach-button"
          @click="onAttachClick"
        >
          <span
            v-if="isUploading"
            class="block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
            aria-hidden="true"
          />
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
            <path d="M21 12.5 12.5 21a5 5 0 0 1-7-7L14 5.5a3.5 3.5 0 0 1 5 5L10.5 19a2 2 0 1 1-3-3L15 8.5" />
          </svg>
        </button>

        <button
          type="button"
          :class="[
            'shrink-0 rounded-md px-2 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50',
            store.guidelineMode
              ? 'bg-primary-600 text-white hover:bg-primary-700'
              : 'bg-transparent text-ink-muted hover:bg-surface hover:text-ink',
          ]"
          :aria-pressed="store.guidelineMode ? 'true' : 'false'"
          :aria-label="store.guidelineMode ? 'Guidelines mode on (toggle off)' : 'Ask guidelines (toggle on)'"
          :title="store.guidelineMode ? 'Guidelines mode on — your message also runs against the guideline RAG.' : 'Toggle to ask a clinical-guideline question; the next turn will retrieve and cite guidelines.'"
          :disabled="store.isSending"
          data-test="guideline-toggle"
          @click="onGuidelineToggle"
        >
          Guidelines
        </button>

        <textarea
          ref="composerEl"
          v-model="draft"
          rows="1"
          placeholder="Ask the co-pilot…"
          class="min-h-[28px] flex-1 resize-none bg-transparent px-1 py-1 text-sm text-ink placeholder:text-ink-muted focus:outline-none"
          :disabled="store.isSending"
          @keydown="onComposerKeydown"
          @input="onComposerInput"
        />

        <button
          type="button"
          class="shrink-0 rounded-md bg-primary-600 p-1.5 text-white transition-colors hover:bg-primary-700 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="draft.trim().length === 0 || store.isSending"
          aria-label="Send message"
          @click="send"
        >
          <svg
            v-if="!store.isSending"
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
