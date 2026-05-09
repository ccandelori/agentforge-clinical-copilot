import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { useAgentTurn, type Citation } from '@/composables/useAgentTurn'
import type { DocumentType } from '@/composables/useDocumentUpload'

/**
 * AgentForge co-pilot store (Wave 3 — real /api/agent/turn wiring).
 *
 * Holds the in-flight conversation, history of past conversations, and
 * orchestrates the BFF round-trip. The previous implementation simulated
 * streaming via setTimeout against a canned reply table; that is gone.
 *
 * Public surface kept stable for components: `messages`, `sendMessage`,
 * `newConversation`, `selectConversation`, `sortedConversations`,
 * `activeConversation`, `activeConversationId`, `hydrate`.
 *
 * Persistence: `sessionStorage` (NOT localStorage) — same key. The
 * recon (sec 6) flagged that dashboard-port keeps zero PHI in
 * localStorage; mirroring that here on the way to real data.
 *
 * Re-exports `Citation` from the composable so existing imports
 * (`@/stores/agentforge`) keep working without churn. Citation kinds
 * were reconciled to dashboard-port's set: `'imaging'` and
 * `'medication'` were dropped in favour of `'med'` and `'allergy'`.
 */

const STORAGE_KEY = 'agentforge-conversations'

export type MessageRole = 'user' | 'assistant'

export type { Citation, IntakeExtraction } from '@/composables/useAgentTurn'

import type { IntakeExtraction } from '@/composables/useAgentTurn'

export interface ChatMessage {
  readonly id: string
  readonly role: MessageRole
  readonly text: string
  readonly createdAt: string
  readonly citations?: readonly Citation[]
  /**
   * Structured intake-form extraction snapshot the sidecar attaches when
   * the turn included a scanned document. Drives the
   * <ExtractionPanel> rendered below the assistant bubble.
   */
  readonly extraction?: IntakeExtraction
  /**
   * Set when the assistant turn failed (network error, timeout, non-2xx
   * from the sidecar). The chat pane styles error bubbles distinctly
   * but otherwise treats them like any other assistant message.
   */
  readonly error?: boolean
}

export interface Conversation {
  readonly id: string
  readonly createdAt: string
  title: string
  messages: ChatMessage[]
}

interface PersistedShape {
  readonly version: 1
  readonly conversations: readonly Conversation[]
  readonly activeConversationId: string | null
}

function nowIso(): string {
  return new Date().toISOString()
}

function makeId(prefix: string): string {
  const rand = Math.random().toString(36).slice(2, 10)
  return `${prefix}-${Date.now().toString(36)}-${rand}`
}

function isCitation(v: unknown): v is Citation {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.id !== 'string') return false
  if (typeof o.source !== 'string') return false
  if (typeof o.excerpt !== 'string') return false
  if (typeof o.date !== 'string') return false
  if (
    o.kind !== 'note'
    && o.kind !== 'lab'
    && o.kind !== 'med'
    && o.kind !== 'problem'
    && o.kind !== 'allergy'
  ) {
    return false
  }
  if (o.provenance !== undefined && typeof o.provenance !== 'string') {
    return false
  }
  return true
}

function isMessage(v: unknown): v is ChatMessage {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.id !== 'string') return false
  if (o.role !== 'user' && o.role !== 'assistant') return false
  if (typeof o.text !== 'string') return false
  if (typeof o.createdAt !== 'string') return false
  if (o.citations !== undefined) {
    if (!Array.isArray(o.citations)) return false
    if (!o.citations.every(isCitation)) return false
  }
  if (o.error !== undefined && typeof o.error !== 'boolean') return false
  return true
}

function isConversation(v: unknown): v is Conversation {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.id !== 'string') return false
  if (typeof o.createdAt !== 'string') return false
  if (typeof o.title !== 'string') return false
  if (!Array.isArray(o.messages)) return false
  if (!o.messages.every(isMessage)) return false
  return true
}

function readPersisted(): PersistedShape | null {
  if (typeof sessionStorage === 'undefined') return null
  let raw: string | null
  try {
    raw = sessionStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
  if (raw === null) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const o = parsed as Record<string, unknown>
    if (o.version !== 1) return null
    if (!Array.isArray(o.conversations)) return null
    if (!o.conversations.every(isConversation)) return null
    if (
      o.activeConversationId !== null
      && typeof o.activeConversationId !== 'string'
    ) {
      return null
    }
    return {
      version: 1,
      conversations: o.conversations,
      activeConversationId: o.activeConversationId,
    }
  } catch {
    return null
  }
}

const ERROR_FALLBACK_TEXT
  = 'I couldn’t reach the agent. Check your connection and try again.'

/**
 * In-memory state for an attachment the clinician has uploaded but not
 * yet sent with a chat message. Held in component-light store state
 * (no persistence — by design, per the no-PHI-in-storage rule).
 *
 * ``docType`` is set at upload time from the filename heuristic
 * (:func:`inferDocType`); the store forwards it as ``doc_type`` on the
 * next turn so the BFF graph dispatches lab PDFs through
 * ``LAB_CONTRACT`` rather than silently defaulting them to intake.
 */
export interface PendingAttachment {
  readonly documentId: string
  readonly filename: string
  readonly docType: DocumentType
}

export const useAgentForgeStore = defineStore('agentforge', () => {
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  /**
   * `true` while a `/api/agent/turn` request is in flight. The composer
   * uses this to disable input/send and the chat pane renders a small
   * "Thinking…" indicator.
   */
  const isSending = ref<boolean>(false)
  const hydrated = ref<boolean>(false)
  /**
   * The most-recent successful upload, waiting to ride the next chat
   * turn. Cleared as soon as ``sendMessage`` puts the id on the wire,
   * so a follow-up message doesn't re-attach the same document. Never
   * persisted — this is transient session state by design.
   */
  const pendingAttachment = ref<PendingAttachment | null>(null)
  /**
   * "Ask guidelines" mode. When ``true``, ``sendMessage`` mirrors the
   * user's text into ``evidence_query`` so the BFF's W2 graph fires
   * the evidence retriever (RAG over clinical guidelines). When
   * ``false`` we send only ``message``; the orchestrator falls back to
   * the W1 iterative chart-Q&A loop and the retriever stays cold —
   * cheaper and faster for chart questions that don't need guidelines.
   *
   * Driven from the toggle next to the attach button in
   * ``AgentChatPane``.
   */
  const guidelineMode = ref<boolean>(false)

  // useRoute() is safe in setup-store callbacks because the store is
  // instantiated lazily by Pinia — by the time a component calls
  // useAgentForgeStore() the router has been provided.
  const route = useRoute()
  const agent = useAgentTurn()

  function hydrate(): void {
    if (hydrated.value) return
    hydrated.value = true
    const persisted = readPersisted()
    if (persisted !== null) {
      conversations.value = persisted.conversations.map((c) => ({
        id: c.id,
        createdAt: c.createdAt,
        title: c.title,
        messages: [...c.messages],
      }))
      activeConversationId.value = persisted.activeConversationId
    }
    // No seed history — production data only. Empty state is the right
    // first impression until the user actually talks to the agent.
  }

  function persist(): void {
    if (typeof sessionStorage === 'undefined') return
    const payload: PersistedShape = {
      version: 1,
      conversations: conversations.value,
      activeConversationId: activeConversationId.value,
    }
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      // Quota or serialization issue — drop silently; in-memory state is
      // canonical.
    }
  }

  const activeConversation = computed<Conversation | null>(() => {
    if (!hydrated.value) hydrate()
    const id = activeConversationId.value
    if (id === null) return null
    return conversations.value.find((c) => c.id === id) ?? null
  })

  const messages = computed<readonly ChatMessage[]>(() => {
    return activeConversation.value?.messages ?? []
  })

  const sortedConversations = computed<readonly Conversation[]>(() => {
    if (!hydrated.value) hydrate()
    return [...conversations.value].sort((a, b) => {
      return b.createdAt.localeCompare(a.createdAt)
    })
  })

  function ensureConversation(): Conversation {
    if (!hydrated.value) hydrate()
    const existing = activeConversation.value
    if (existing !== null) return existing
    const created: Conversation = {
      id: makeId('conv'),
      createdAt: nowIso(),
      title: 'New conversation',
      messages: [],
    }
    conversations.value = [created, ...conversations.value]
    activeConversationId.value = created.id
    // CRITICAL: return the proxy version that Vue's deep reactivity
    // wrapped when the array was assigned. Returning the local `created`
    // raw object means subsequent `conv.messages = [...]` mutations
    // don't propagate through Pinia's reactivity, so the assistant
    // reply on the very first turn doesn't render until the next
    // state change forces a re-evaluation.
    return activeConversation.value ?? created
  }

  function newConversation(): void {
    if (!hydrated.value) hydrate()
    const created: Conversation = {
      id: makeId('conv'),
      createdAt: nowIso(),
      title: 'New conversation',
      messages: [],
    }
    conversations.value = [created, ...conversations.value]
    activeConversationId.value = created.id
    isSending.value = false
    persist()
  }

  function selectConversation(id: string): void {
    if (!hydrated.value) hydrate()
    if (!conversations.value.some((c) => c.id === id)) return
    activeConversationId.value = id
    isSending.value = false
    persist()
  }

  /**
   * Derive the patient UUID for the BFF from the active route. vue-ui's
   * patient detail route is `/patients/:id`; if we're elsewhere
   * (dashboard, calendar, settings) we send `undefined` and let the
   * sidecar handle the no-patient case.
   */
  function currentPatientUuid(): string | undefined {
    if (route?.name !== 'patient-dashboard') return undefined
    const id = route.params['id']
    if (typeof id !== 'string' || id.length === 0) return undefined
    return id
  }

  async function sendMessage(text: string): Promise<void> {
    const trimmed = text.trim()
    if (trimmed.length === 0) return
    if (isSending.value) return

    const conv = ensureConversation()

    const userMsg: ChatMessage = {
      id: makeId('m'),
      role: 'user',
      text: trimmed,
      createdAt: nowIso(),
    }
    conv.messages = [...conv.messages, userMsg]
    if (conv.title === 'New conversation') {
      conv.title
        = trimmed.length > 48 ? `${trimmed.slice(0, 45)}...` : trimmed
    }
    persist()

    // Snapshot + clear the pending attachment up front. ``document_id``
    // rides exactly one turn; clearing it before the (async) network
    // round-trip avoids any chance of a follow-up ``sendMessage`` (the
    // user re-typing while we're in-flight is gated by ``isSending``,
    // but defence-in-depth) re-attaching the same upload.
    const attachmentForTurn = pendingAttachment.value
    pendingAttachment.value = null

    isSending.value = true
    try {
      const result = await agent.send({
        message: trimmed,
        ...(currentPatientUuid() !== undefined
          ? { patient_uuid: currentPatientUuid() as string }
          : {}),
        session_id: conv.id,
        ...(attachmentForTurn !== null
          ? {
              document_id: attachmentForTurn.documentId,
              doc_type: attachmentForTurn.docType,
            }
          : {}),
        ...(guidelineMode.value
          ? { evidence_query: trimmed }
          : {}),
      })
      const assistantMsg: ChatMessage = {
        id: makeId('m'),
        role: 'assistant',
        text: result.reply,
        createdAt: nowIso(),
        ...(result.citations.length > 0
          ? { citations: result.citations }
          : {}),
        ...(result.extraction !== undefined
          ? { extraction: result.extraction }
          : {}),
      }
      conv.messages = [...conv.messages, assistantMsg]
    } catch (caught) {
      const friendly
        = caught instanceof Error && caught.message.length > 0
          ? caught.message
          : ERROR_FALLBACK_TEXT
      const errorMsg: ChatMessage = {
        id: makeId('m'),
        role: 'assistant',
        text: friendly,
        createdAt: nowIso(),
        error: true,
      }
      conv.messages = [...conv.messages, errorMsg]
    } finally {
      isSending.value = false
      persist()
    }
  }

  function setPendingAttachment(attachment: PendingAttachment): void {
    pendingAttachment.value = attachment
  }

  function clearPendingAttachment(): void {
    pendingAttachment.value = null
  }

  function setGuidelineMode(on: boolean): void {
    guidelineMode.value = on
  }

  function toggleGuidelineMode(): void {
    guidelineMode.value = !guidelineMode.value
  }

  return {
    conversations,
    sortedConversations,
    activeConversationId,
    activeConversation,
    messages,
    isSending,
    pendingAttachment,
    guidelineMode,
    hydrate,
    newConversation,
    selectConversation,
    sendMessage,
    setPendingAttachment,
    clearPendingAttachment,
    setGuidelineMode,
    toggleGuidelineMode,
  }
})
