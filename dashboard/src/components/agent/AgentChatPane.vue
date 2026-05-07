<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import type { Citation } from '@/composables/useAgentTurn'
import { useAgentTurn } from '@/composables/useAgentTurn'
import {
  useAgentDrawer,
  type AgentMessage,
  type AgentMode,
} from '@/stores/agentDrawer'

import CitationPill from './CitationPill.vue'

// AgentChatPane — the Chat tab. Owns:
//
//  * Empty state with 4 scope-aware suggestion chips that fill the
//    composer when picked.
//  * Message list with user/assistant bubbles. Assistant bubbles
//    surface a row of CitationPills if the message carries citations,
//    plus a copy-on-hover button.
//  * Auto-scroll on new message; pauses if the user scrolls up >50px
//    from the bottom; resumes when they get back within 50px.
//  * Composer: textarea, Enter sends / Shift+Enter newline, attach
//    button (no-op for now), send button (disabled while in flight or
//    when input is empty / out-of-scope).
//
// Wiring: send() goes through the existing real `/api/agent/turn`
// composable. Citations on the response are parsed by the composable
// and stored on the assistant message via `addAssistantTurn(text, cites)`.

const emit = defineEmits<{
  (e: 'citation-click', id: string): void
}>()

const store = useAgentDrawer()
const agentTurn = useAgentTurn()

const draft = ref<string>('')
const composerEl = ref<HTMLTextAreaElement | null>(null)
const scrollEl = ref<HTMLDivElement | null>(null)
const autoScrollPaused = ref<boolean>(false)

const SUGGESTIONS_BY_MODE: Readonly<Record<AgentMode, readonly string[]>> = {
  chart: [
    'Summarize last visit',
    'Suggest differential',
    'Show abnormal labs',
    'Draft note',
  ],
  intake: [
    'Extract chief complaint',
    'Suggest ROS questions',
    'Draft HPI',
    'Summarize',
  ],
  research: [
    'Summarize this paper',
    'Find related guidelines',
    'Draft talking points',
    'Compare evidence',
  ],
}

const messages = computed<readonly AgentMessage[]>(
  () => store.currentMessages,
)

const showEmptyState = computed<boolean>(() => messages.value.length === 0)

const suggestions = computed<readonly string[]>(
  () => SUGGESTIONS_BY_MODE[store.mode],
)

const agentReady = computed<boolean>(
  () =>
    store.mode === 'chart'
    && store.canChart
    && store.pendingPatientChange === null,
)

const isLoading = computed<boolean>(
  () => agentTurn.status.value === 'loading',
)

const inputDisabled = computed<boolean>(
  () => !agentReady.value || isLoading.value,
)

const sendDisabled = computed<boolean>(
  () => inputDisabled.value || draft.value.trim().length === 0,
)

const placeholder = computed<string>(() => {
  if (store.pendingPatientChange !== null) return 'Resolve the patient change first.'
  if (!agentReady.value) {
    if (store.mode === 'chart' && !store.canChart) {
      return 'Open a patient chart to chat with the agent.'
    }
    if (store.mode === 'intake' && !store.canIntake) {
      return 'Open an intake document to chat with the agent.'
    }
    return 'Research mode is not yet wired to the agent.'
  }
  if (isLoading.value) return 'Thinking…'
  return 'Ask AgentForge…'
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
  // 50px slack — drawer header/footer transitions can shift the layout
  // by a handful of pixels; without slack the pause flag latches on
  // every nextTick.
  autoScrollPaused.value = distanceFromBottom > 50
}

watch(
  () => messages.value.length,
  () => {
    autoScrollPaused.value = false
    void nextTick(() => scrollToBottom(true))
  },
)

watch(
  () => store.currentScopeId,
  () => {
    autoScrollPaused.value = false
    void nextTick(() => scrollToBottom(true))
  },
)

onMounted(() => {
  void nextTick(() => scrollToBottom(true))
})

function resizeComposer(): void {
  const el = composerEl.value
  if (el === null) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 160)}px`
}

function send(): void {
  if (sendDisabled.value) return
  const text = draft.value.trim()
  if (text === '') return

  // store.activePatient is the FHIR Patient resource UUID (the
  // dashboard route's :pid param). The BFF route resolves it
  // server-side into the integer pid the agent JWT carries.
  const patientUuid = store.activePatient
  if (patientUuid === null || patientUuid === '') return

  store.addUserTurn(text)
  draft.value = ''
  void nextTick(() => resizeComposer())

  void agentTurn
    .send({
      message: text,
      patient_uuid: patientUuid,
      session_id: store.currentScopeId,
    })
    .then((result) => {
      store.addAssistantTurn(result.reply, result.citations)
    })
    .catch((err: unknown) => {
      const message = err instanceof Error ? err.message : String(err)
      // Error replies have no citations — surface as a plain message.
      store.addAssistantTurn(`Error: ${message}. Please try again.`)
    })
}

function onComposerKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault()
    send()
  }
}

function pickSuggestion(s: string): void {
  if (inputDisabled.value) return
  draft.value = s
  void nextTick(() => {
    composerEl.value?.focus()
    resizeComposer()
  })
}

function onCitationClick(id: string): void {
  emit('citation-click', id)
}

// Per-message copy-on-hover state. Keyed by message id, true while the
// "Copied" affordance is pinned (1.5s window).
const copiedIds = ref<Set<string>>(new Set())
const copyTimers = new Map<string, ReturnType<typeof setTimeout>>()

async function copyMessage(message: AgentMessage): Promise<void> {
  if (typeof navigator === 'undefined' || !navigator.clipboard) return
  try {
    await navigator.clipboard.writeText(message.text)
    const next = new Set(copiedIds.value)
    next.add(message.id)
    copiedIds.value = next
    const existing = copyTimers.get(message.id)
    if (existing !== undefined) clearTimeout(existing)
    copyTimers.set(
      message.id,
      setTimeout(() => {
        const cleared = new Set(copiedIds.value)
        cleared.delete(message.id)
        copiedIds.value = cleared
        copyTimers.delete(message.id)
      }, 1500),
    )
  } catch {
    // Clipboard not available — silently ignore.
  }
}

function isCopied(id: string): boolean {
  return copiedIds.value.has(id)
}

function citationsFor(message: AgentMessage): readonly Citation[] {
  return message.citations ?? []
}
</script>

<template>
  <div class="agent-chat-pane d-flex flex-column h-100">
    <div
      ref="scrollEl"
      class="agent-chat-pane__scroll flex-grow-1 overflow-auto px-3 py-3"
      data-test="agent-chat-scroll"
      @scroll="onScroll"
    >
      <div
        v-if="showEmptyState"
        class="d-flex flex-column align-items-center justify-content-center h-100 gap-3 text-center py-3"
        data-test="agent-chat-empty"
      >
        <div
          class="agent-chat-pane__empty-icon d-flex align-items-center justify-content-center rounded-3"
          aria-hidden="true"
        >
          <i class="bi bi-stars"></i>
        </div>
        <div>
          <h2 class="h6 mb-1">
            How can I help with this patient/encounter?
          </h2>
          <p class="small mb-0 text-body-secondary">
            AgentForge will cite the chart sources behind every answer.
          </p>
        </div>
        <div
          class="d-flex flex-column gap-2 w-100"
          style="max-width: 22rem;"
        >
          <button
            v-for="s in suggestions"
            :key="s"
            type="button"
            class="agent-chat-pane__suggestion btn text-start"
            :disabled="inputDisabled"
            data-test="agent-suggestion-chip"
            @click="pickSuggestion(s)"
          >
            {{ s }}
          </button>
        </div>
      </div>

      <ul
        v-else
        class="list-unstyled mb-0 d-flex flex-column gap-3"
        data-test="agent-message-list"
      >
        <li
          v-for="msg in messages"
          :key="msg.id"
          class="agent-chat-pane__row d-flex"
          :class="msg.role === 'user' ? 'justify-content-end' : 'justify-content-start'"
        >
          <div
            class="d-flex flex-column gap-2"
            style="max-width: 88%;"
            :class="msg.role === 'user' ? 'align-items-end' : 'align-items-start'"
          >
            <div
              class="agent-chat-pane__bubble rounded-3 px-3 py-2"
              :class="
                msg.role === 'user'
                  ? 'agent-chat-pane__bubble--user'
                  : 'agent-chat-pane__bubble--assistant'
              "
              data-test="agent-message-bubble"
            >
              <span class="agent-chat-pane__text">{{ msg.text }}</span>
            </div>

            <div
              v-if="msg.role === 'assistant' && citationsFor(msg).length > 0"
              class="d-flex flex-wrap gap-2"
              data-test="agent-message-citations"
            >
              <CitationPill
                v-for="(c, idx) in citationsFor(msg)"
                :key="c.id"
                :citation="c"
                :index="idx"
                @select="onCitationClick"
              />
            </div>

            <div
              v-if="msg.role === 'assistant'"
              class="agent-chat-pane__actions"
            >
              <button
                type="button"
                class="btn btn-link btn-sm p-0 d-inline-flex align-items-center gap-1"
                :aria-label="isCopied(msg.id) ? 'Copied' : 'Copy message'"
                data-test="agent-message-copy"
                @click="copyMessage(msg)"
              >
                <i
                  v-if="!isCopied(msg.id)"
                  class="bi bi-clipboard"
                  aria-hidden="true"
                ></i>
                <i
                  v-else
                  class="bi bi-check2 text-success"
                  aria-hidden="true"
                ></i>
                <span class="small">{{ isCopied(msg.id) ? 'Copied' : 'Copy' }}</span>
              </button>
            </div>
          </div>
        </li>
      </ul>
    </div>

    <footer class="agent-chat-pane__footer border-top px-3 py-2">
      <div class="agent-chat-pane__composer d-flex align-items-end gap-2 rounded-3 px-2 py-1">
        <button
          type="button"
          class="btn btn-link p-1 text-body-secondary"
          aria-label="Attach file (coming soon)"
          title="Attach file (coming soon)"
          data-test="agent-attach"
          disabled
        >
          <i class="bi bi-paperclip" aria-hidden="true"></i>
        </button>

        <textarea
          ref="composerEl"
          v-model="draft"
          rows="1"
          class="agent-chat-pane__textarea form-control form-control-sm border-0 bg-transparent shadow-none"
          :placeholder="placeholder"
          :disabled="inputDisabled"
          data-test="agent-input"
          @keydown="onComposerKeydown"
          @input="resizeComposer"
        ></textarea>

        <button
          type="button"
          class="btn btn-primary btn-sm d-inline-flex align-items-center justify-content-center"
          :disabled="sendDisabled"
          aria-label="Send message"
          data-test="agent-send"
          @click="send"
        >
          <i
            v-if="!isLoading"
            class="bi bi-send"
            aria-hidden="true"
          ></i>
          <span
            v-else
            class="spinner-border spinner-border-sm"
            role="status"
            aria-hidden="true"
          ></span>
        </button>
      </div>
      <p class="mb-0 mt-1 text-body-secondary" style="font-size: 0.7rem;">
        Enter to send · Shift+Enter for new line
      </p>
    </footer>
  </div>
</template>

<style scoped>
.agent-chat-pane__empty-icon {
  width: 3rem;
  height: 3rem;
  background-color: var(--accent-soft);
  color: var(--accent);
  font-size: 1.25rem;
}

.agent-chat-pane__suggestion {
  border: 1px solid var(--line);
  background-color: var(--surface);
  color: var(--ink);
  border-radius: 0.75rem;
  padding: 0.625rem 0.75rem;
  font-size: 0.875rem;
}

.agent-chat-pane__suggestion:hover:not(:disabled),
.agent-chat-pane__suggestion:focus-visible {
  border-color: var(--accent);
  background-color: var(--accent-soft);
}

.agent-chat-pane__suggestion:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.agent-chat-pane__bubble {
  font-size: 0.875rem;
  line-height: 1.4;
  word-wrap: break-word;
}

.agent-chat-pane__bubble--user {
  background-color: var(--bs-primary);
  color: #fff;
  border: 1px solid var(--bs-primary);
}

.agent-chat-pane__bubble--assistant {
  background-color: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line);
}

.agent-chat-pane__text {
  white-space: pre-wrap;
}

.agent-chat-pane__actions {
  font-size: 0.7rem;
  opacity: 0;
  transition: opacity 120ms ease;
}

.agent-chat-pane__row:hover .agent-chat-pane__actions,
.agent-chat-pane__actions:focus-within {
  opacity: 1;
}

.agent-chat-pane__composer {
  border: 1px solid var(--line);
  background-color: var(--surface-2);
}

.agent-chat-pane__composer:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgb(var(--accent-rgb) / 0.2);
}

.agent-chat-pane__textarea {
  resize: none;
  min-height: 28px;
  max-height: 160px;
}

.agent-chat-pane__textarea:focus {
  box-shadow: none;
  background-color: transparent;
}
</style>
