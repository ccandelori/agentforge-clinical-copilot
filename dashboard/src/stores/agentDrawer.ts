import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'

// Pinia store for the AgentForge drawer (T38.10).
//
// Owns drawer open/closed state, the active mode (Chart/Intake/Research),
// and the in-memory conversation registry keyed by scope. Conversations
// outlive their mode/patient context but die on a page reload.
//
// Patient-change conflict policy: when in Chart mode with progress and
// the active patient is replaced by a different patient, the change is
// staged as `pendingPatientChange`. The drawer renders a hard-interrupt
// overlay; resolution happens through `resolvePatientChange()`. Any
// other transition (Research mode, no progress yet, or clearing the
// patient entirely) flows through immediately.

export type AgentMode = 'chart' | 'intake' | 'research'
export type MessageRole = 'user' | 'assistant'

export interface AgentMessage {
  role: MessageRole
  text: string
}

export interface PendingPatientChange {
  from: string
  to: string
}

type ResolveAction = 'switch' | 'stay' | 'fresh'

const RESEARCH_SCOPE = 'research:global'
const chartScope = (pid: string): string => `chart:${pid}`
const intakeScope = (documentId: string): string => `intake:${documentId}`

export const useAgentDrawer = defineStore('agentDrawer', () => {
  const open = ref<boolean>(false)
  const mode = ref<AgentMode>('research')
  const activePatient = ref<string | null>(null)
  const activeDocument = ref<string | null>(null)
  const pendingPatientChange = ref<PendingPatientChange | null>(null)

  // Reactive Map<scope, AgentMessage[]>. We use a plain object so Vue's
  // reactivity tracks new keys; callers should never mutate entries directly.
  const conversations = reactive<Record<string, AgentMessage[]>>({})

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

  const currentMessages = computed<AgentMessage[]>(
    () => conversations[currentScopeId.value] ?? [],
  )

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
    ensureScope(currentScopeId.value).push({ role: 'user', text })
  }
  function addAssistantTurn(text: string): void {
    ensureScope(currentScopeId.value).push({ role: 'assistant', text })
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

  return {
    // state
    open,
    mode,
    activePatient,
    activeDocument,
    pendingPatientChange,
    conversations,
    // computed
    canChart,
    canIntake,
    currentScopeId,
    currentMessages,
    // actions
    openDrawer,
    close,
    toggle,
    setMode,
    setActivePatient,
    setActiveDocument,
    addUserTurn,
    addAssistantTurn,
    hasStaleConversation,
    resolvePatientChange,
  }
})
