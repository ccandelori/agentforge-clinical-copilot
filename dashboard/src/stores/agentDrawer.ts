import { defineStore } from 'pinia'
import { computed, reactive, ref, watch } from 'vue'

import type { Citation } from '@/composables/useAgentTurn'

// Pinia store for the AgentForge drawer (T38.10 + T38.11 + UX polish).
//
// Owns drawer open/closed state, the active mode (Chart/Intake/Research),
// the active drawer tab (Chat/Citations/History — orthogonal to mode),
// and the in-memory conversation registry keyed by scope. Active
// conversations outlive their mode/patient context but die on a page
// reload; the *history* of past conversations is snapshotted into
// sessionStorage on each "new conversation" so the History tab still
// has something to show after a navigation. sessionStorage (NOT
// localStorage) is used because (a) the dashboard-port keeps zero PHI
// in localStorage and (b) snapshots may include patient prompts.
//
// Patient-change conflict policy: when in Chart mode with progress and
// the active patient is replaced by a different patient, the change is
// staged as `pendingPatientChange`. The drawer renders a hard-interrupt
// overlay; resolution happens through `resolvePatientChange()`. Any
// other transition (Research mode, no progress yet, or clearing the
// patient entirely) flows through immediately.

export type AgentMode = 'chart' | 'intake' | 'research'
export type AgentTab = 'chat' | 'citations' | 'history'
export type MessageRole = 'user' | 'assistant'

export interface AgentMessage {
  readonly id: string
  readonly role: MessageRole
  readonly text: string
  readonly createdAt: string
  readonly citations?: readonly Citation[]
}

export interface PendingPatientChange {
  from: string
  to: string
}

/**
 * A snapshot of a finished conversation, stored under sessionStorage
 * for the History tab. We snapshot on `newConversation()` (the user
 * explicitly archived the current chat) — not on every turn — so
 * history reflects deliberate save points, not transient state.
 */
export interface ConversationSnapshot {
  readonly id: string
  readonly scopeId: string
  readonly mode: AgentMode
  readonly startedAt: string
  readonly messages: readonly AgentMessage[]
}

type ResolveAction = 'switch' | 'stay' | 'fresh'

const RESEARCH_SCOPE = 'research:global'
const HISTORY_STORAGE_KEY = 'agent-drawer:conversation-history'
const HISTORY_VERSION = 1
const MAX_HISTORY_ITEMS = 50

const chartScope = (pid: string): string => `chart:${pid}`
const intakeScope = (documentId: string): string => `intake:${documentId}`

function nowIso(): string {
  return new Date().toISOString()
}

function makeId(prefix: string): string {
  const rand = Math.random().toString(36).slice(2, 10)
  return `${prefix}-${Date.now().toString(36)}-${rand}`
}

interface PersistedHistory {
  readonly version: number
  readonly snapshots: readonly ConversationSnapshot[]
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
  if (
    o.provenance !== undefined
    && typeof o.provenance !== 'string'
  ) {
    return false
  }
  return true
}

function isMessage(v: unknown): v is AgentMessage {
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
  return true
}

function isSnapshot(v: unknown): v is ConversationSnapshot {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  if (typeof o.id !== 'string') return false
  if (typeof o.scopeId !== 'string') return false
  if (o.mode !== 'chart' && o.mode !== 'intake' && o.mode !== 'research') {
    return false
  }
  if (typeof o.startedAt !== 'string') return false
  if (!Array.isArray(o.messages)) return false
  if (!o.messages.every(isMessage)) return false
  return true
}

function readHistoryFromSession(): readonly ConversationSnapshot[] {
  if (typeof sessionStorage === 'undefined') return []
  let raw: string | null
  try {
    raw = sessionStorage.getItem(HISTORY_STORAGE_KEY)
  } catch {
    return []
  }
  if (raw === null) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return []
    const o = parsed as Record<string, unknown>
    if (o.version !== HISTORY_VERSION) return []
    if (!Array.isArray(o.snapshots)) return []
    return o.snapshots.filter(isSnapshot)
  } catch {
    return []
  }
}

function writeHistoryToSession(snapshots: readonly ConversationSnapshot[]): void {
  if (typeof sessionStorage === 'undefined') return
  const payload: PersistedHistory = {
    version: HISTORY_VERSION,
    snapshots,
  }
  try {
    sessionStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // Quota / private mode — drop silently. In-memory remains canonical.
  }
}

export const useAgentDrawer = defineStore('agentDrawer', () => {
  const open = ref<boolean>(false)
  const mode = ref<AgentMode>('research')
  const activeTab = ref<AgentTab>('chat')
  const activePatient = ref<string | null>(null)
  const activeDocument = ref<string | null>(null)
  const pendingPatientChange = ref<PendingPatientChange | null>(null)

  // Reactive object<scope, AgentMessage[]>. We use a plain object so Vue's
  // reactivity tracks new keys; callers should never mutate entries directly.
  const conversations = reactive<Record<string, AgentMessage[]>>({})

  // History of finished/archived conversations, hydrated from sessionStorage
  // on construction and persisted on every mutation.
  const conversationHistory = ref<ConversationSnapshot[]>([
    ...readHistoryFromSession(),
  ])

  watch(
    conversationHistory,
    (next) => {
      writeHistoryToSession(next)
    },
    { deep: true },
  )

  function ensureScope(scope: string): AgentMessage[] {
    if (conversations[scope] === undefined) {
      conversations[scope] = []
    }
    return conversations[scope]
  }

  const canChart = computed<boolean>(() => activePatient.value !== null)
  const canIntake = computed<boolean>(() => activeDocument.value !== null)

  const currentScopeId = computed<string>(() => {
    if (mode.value === 'chart' && activePatient.value !== null) {
      return chartScope(activePatient.value)
    }
    if (mode.value === 'intake' && activeDocument.value !== null) {
      return intakeScope(activeDocument.value)
    }
    return RESEARCH_SCOPE
  })

  const currentMessages = computed<readonly AgentMessage[]>(
    () => conversations[currentScopeId.value] ?? [],
  )

  /**
   * Union of citations from every assistant message in the current scope,
   * deduplicated by id. Drives the Citations tab.
   */
  const currentCitations = computed<readonly Citation[]>(() => {
    const out: Citation[] = []
    const seen = new Set<string>()
    for (const msg of currentMessages.value) {
      const cites = msg.citations
      if (cites === undefined) continue
      for (const c of cites) {
        if (seen.has(c.id)) continue
        seen.add(c.id)
        out.push(c)
      }
    }
    return out
  })

  function openDrawer(): void {
    open.value = true
  }
  function close(): void {
    open.value = false
  }
  function toggle(): void {
    open.value = !open.value
  }

  function setMode(next: AgentMode): void {
    if (next === 'chart' && !canChart.value) {
      return
    }
    if (next === 'intake' && !canIntake.value) {
      return
    }
    mode.value = next
  }

  function setActiveTab(tab: AgentTab): void {
    activeTab.value = tab
  }

  function chartHasProgress(pid: string | null): boolean {
    if (pid === null) return false
    const scope = chartScope(pid)
    return (conversations[scope]?.length ?? 0) > 0
  }

  function setActivePatient(pid: string | null): void {
    if (pid === activePatient.value) {
      return
    }

    // Clearing the patient never raises a conflict — it's typically the
    // result of leaving /patient/:pid; preserve any chart progress in
    // its scope and demote out of Chart mode.
    if (pid === null) {
      activePatient.value = null
      if (mode.value === 'chart') {
        mode.value = 'research'
      }
      return
    }

    // Going from no-patient to having one is also unconditional.
    if (activePatient.value === null) {
      activePatient.value = pid
      return
    }

    // Different patient. The conflict policy only fires when we're
    // currently in Chart mode AND the outgoing chart conversation has
    // progress. Anything else: switch immediately.
    if (mode.value !== 'chart' || !chartHasProgress(activePatient.value)) {
      activePatient.value = pid
      return
    }

    pendingPatientChange.value = { from: activePatient.value, to: pid }
  }

  function setActiveDocument(documentId: string | null): void {
    activeDocument.value = documentId
    if (documentId === null && mode.value === 'intake') {
      mode.value = 'research'
    }
  }

  function addUserTurn(text: string): void {
    ensureScope(currentScopeId.value).push({
      id: makeId('m'),
      role: 'user',
      text,
      createdAt: nowIso(),
    })
  }

  /**
   * Append an assistant turn. Citations are optional — sidecar payloads
   * that omit the field land here as `undefined`, which is preserved in
   * the message so the chat pane can `?? []` on read.
   */
  function addAssistantTurn(
    text: string,
    citations?: readonly Citation[],
  ): void {
    const message: AgentMessage = {
      id: makeId('m'),
      role: 'assistant',
      text,
      createdAt: nowIso(),
      ...(citations !== undefined ? { citations } : {}),
    }
    ensureScope(currentScopeId.value).push(message)
  }

  function hasStaleConversation(pid: string): boolean {
    if (pid === activePatient.value) {
      return false
    }
    return chartHasProgress(pid)
  }

  function resolvePatientChange(action: ResolveAction): void {
    const pending = pendingPatientChange.value
    if (pending === null) {
      return
    }

    if (action === 'stay') {
      pendingPatientChange.value = null
      return
    }

    if (action === 'fresh') {
      conversations[chartScope(pending.to)] = []
    }

    // 'switch' and 'fresh' both end with activePatient on the target.
    activePatient.value = pending.to
    pendingPatientChange.value = null
  }

  /**
   * Snapshot the current scope's conversation into history (if it has
   * any messages), then clear that scope so the chat tab renders the
   * empty state. Idempotent on an empty scope.
   */
  function newConversation(): void {
    const scope = currentScopeId.value
    const messages = conversations[scope]
    if (messages !== undefined && messages.length > 0) {
      const firstCreated = messages[0]?.createdAt ?? nowIso()
      const snapshot: ConversationSnapshot = {
        id: makeId('conv'),
        scopeId: scope,
        mode: mode.value,
        startedAt: firstCreated,
        messages: [...messages],
      }
      // Newest-first; cap at MAX_HISTORY_ITEMS.
      conversationHistory.value = [
        snapshot,
        ...conversationHistory.value,
      ].slice(0, MAX_HISTORY_ITEMS)
    }
    conversations[scope] = []
    activeTab.value = 'chat'
  }

  /**
   * Restore a snapshotted conversation into the *current* scope (not
   * the snapshot's original scope) — that's the contract the History
   * pane promises: clicking an item drops the conversation into
   * wherever the user currently is. The snapshot stays in history
   * (no destructive move) so the user can re-pick it from a different
   * scope if they want.
   */
  function selectConversation(snapshotId: string): void {
    const snap = conversationHistory.value.find((s) => s.id === snapshotId)
    if (snap === undefined) return
    conversations[currentScopeId.value] = [...snap.messages]
    activeTab.value = 'chat'
  }

  return {
    // state
    open,
    mode,
    activeTab,
    activePatient,
    activeDocument,
    pendingPatientChange,
    conversations,
    conversationHistory,
    // computed
    canChart,
    canIntake,
    currentScopeId,
    currentMessages,
    currentCitations,
    // actions
    openDrawer,
    close,
    toggle,
    setMode,
    setActiveTab,
    setActivePatient,
    setActiveDocument,
    addUserTurn,
    addAssistantTurn,
    hasStaleConversation,
    resolvePatientChange,
    newConversation,
    selectConversation,
  }
})
