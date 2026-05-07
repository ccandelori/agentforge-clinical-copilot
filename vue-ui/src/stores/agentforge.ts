import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

/**
 * AgentForge co-pilot store (Wave 2f).
 *
 * Holds the in-flight conversation, history of past conversations, and
 * orchestrates the mock streaming reply ("typewriter") cycle. Persists
 * to localStorage under `agentforge-conversations`.
 *
 * Wave 3 will swap the mock streaming for a real call into the sidecar
 * `/api/agent/turn` endpoint; the surface (`messages`, `sendMessage`,
 * `newConversation`, `selectConversation`) should remain stable.
 */

const STORAGE_KEY = 'agentforge-conversations'

export type MessageRole = 'user' | 'assistant'

export interface Citation {
  readonly id: string
  readonly source: string
  readonly excerpt: string
  readonly date: string
  readonly kind: 'note' | 'lab' | 'imaging' | 'medication' | 'problem'
}

export interface ChatMessage {
  readonly id: string
  readonly role: MessageRole
  readonly text: string
  readonly createdAt: string
  readonly citations?: readonly Citation[]
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

const CANNED_REPLIES: readonly string[] = [
  'Based on the most recent visit, the patient is on lisinopril 10 mg daily for hypertension and metformin 500 mg twice daily for type 2 diabetes. Last A1C was 6.8% (slightly above target). I would recommend reviewing diet adherence and considering a small metformin titration if A1C remains elevated at the next draw.',
  'Reviewing the labs you flagged: LDL is 128 mg/dL (target <100) and triglycerides are 155 mg/dL. With the existing diagnosis of hyperlipidemia, intensifying atorvastatin from 20 mg to 40 mg nightly is reasonable. Recheck a lipid panel in 8-12 weeks.',
  'Differential for the presenting complaint of fatigue plus an A1C of 6.8%: poorly controlled type 2 diabetes (most likely), hypothyroidism, anemia, depression, and obstructive sleep apnea. A TSH and CBC have already resulted within range; an Epworth Sleepiness Scale would help screen for OSA.',
  'Drafted note from your dictation: "Patient returns for diabetes follow-up. Reports good adherence to metformin. Denies polyuria or polydipsia. BP 128/78. A1C 6.8%. Plan: continue metformin, reinforce diet and exercise, recheck A1C in 3 months." Want me to refine the tone or add an assessment block?',
  'Abnormal labs over the last 90 days: HbA1c 6.8% (high), fasting glucose 102 mg/dL (high), LDL 128 mg/dL (high), triglycerides 155 mg/dL (high). Sodium, potassium, creatinine and TSH are all within range. Hemoglobin is normal at 13.5 g/dL.',
  'Summary of last visit (Office Visit, Dr. Patel): chief complaint was a routine follow-up for hypertension. BP was well-controlled at 128/78 on lisinopril 10 mg. No new concerns reported. Continue current regimen, recheck BP in 3 months.',
]

const CANNED_CITATIONS: ReadonlyArray<readonly Citation[]> = [
  [
    {
      id: 'c-001',
      source: 'Progress Note',
      excerpt:
        'BP well controlled at 128/78 on lisinopril 10 mg daily. Continue current regimen.',
      date: '2024-09-12',
      kind: 'note',
    },
    {
      id: 'c-002',
      source: 'Lab Result · HbA1c',
      excerpt: 'HbA1c 6.8% (reference 4.0-5.6%). Flagged HIGH.',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-003',
      source: 'Active Medication',
      excerpt: 'Metformin 500 mg PO BID, prescribed by Dr. Patel.',
      date: '2024-06-04',
      kind: 'medication',
    },
  ],
  [
    {
      id: 'c-101',
      source: 'Lab Result · Lipid Panel',
      excerpt: 'LDL 128 mg/dL (target <100), Triglycerides 155 mg/dL (target <150).',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-102',
      source: 'Problem List',
      excerpt: 'E78.5 Hyperlipidemia, unspecified — active since 2023-11-04.',
      date: '2023-11-04',
      kind: 'problem',
    },
  ],
  [
    {
      id: 'c-201',
      source: 'Lab Result · TSH',
      excerpt: 'TSH 2.4 mIU/L (reference 0.4-4.0). Within range.',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-202',
      source: 'Lab Result · CBC',
      excerpt: 'Hemoglobin 13.5 g/dL (reference 12.0-15.5). Within range.',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-203',
      source: 'Problem List',
      excerpt: 'E11.9 Type 2 diabetes mellitus without complications — active.',
      date: '2023-05-12',
      kind: 'problem',
    },
    {
      id: 'c-204',
      source: 'Progress Note',
      excerpt: 'Patient denies snoring or witnessed apnea. No prior sleep study on file.',
      date: '2024-09-12',
      kind: 'note',
    },
  ],
  [
    {
      id: 'c-301',
      source: 'Audio Dictation',
      excerpt: 'Approximately 90 seconds of provider dictation captured during the encounter.',
      date: '2024-09-12',
      kind: 'note',
    },
    {
      id: 'c-302',
      source: 'Vitals',
      excerpt: 'BP 128/78, HR 72, T 36.8 C, SpO2 98%.',
      date: '2024-09-12',
      kind: 'note',
    },
  ],
  [
    {
      id: 'c-401',
      source: 'Lab Result · HbA1c',
      excerpt: 'HbA1c 6.8% (HIGH).',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-402',
      source: 'Lab Result · Glucose',
      excerpt: 'Fasting glucose 102 mg/dL (HIGH, reference 70-99).',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-403',
      source: 'Lab Result · LDL',
      excerpt: 'LDL 128 mg/dL (HIGH, target <100).',
      date: '2024-08-30',
      kind: 'lab',
    },
    {
      id: 'c-404',
      source: 'Lab Result · Triglycerides',
      excerpt: 'Triglycerides 155 mg/dL (HIGH, target <150).',
      date: '2024-08-30',
      kind: 'lab',
    },
  ],
  [
    {
      id: 'c-501',
      source: 'Encounter · Office Visit',
      excerpt: 'Routine follow-up for hypertension with Dr. Patel. BP 128/78.',
      date: '2024-09-12',
      kind: 'note',
    },
    {
      id: 'c-502',
      source: 'Active Medication',
      excerpt: 'Lisinopril 10 mg PO daily.',
      date: '2024-06-04',
      kind: 'medication',
    },
  ],
]

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
  return (
    typeof o.id === 'string'
    && typeof o.source === 'string'
    && typeof o.excerpt === 'string'
    && typeof o.date === 'string'
    && (o.kind === 'note'
      || o.kind === 'lab'
      || o.kind === 'imaging'
      || o.kind === 'medication'
      || o.kind === 'problem')
  )
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
  if (typeof localStorage === 'undefined') return null
  const raw = localStorage.getItem(STORAGE_KEY)
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

function buildSeedHistory(): Conversation[] {
  const samples: ReadonlyArray<{ readonly title: string; readonly daysAgo: number; readonly first: string; readonly reply: number }> = [
    { title: 'Summarize last visit', daysAgo: 1, first: 'Summarize the last office visit for me.', reply: 5 },
    { title: 'Abnormal labs review', daysAgo: 2, first: 'Show me abnormal labs from the last 90 days.', reply: 4 },
    { title: 'Lipid management', daysAgo: 3, first: 'Suggest next steps on the elevated LDL.', reply: 1 },
    { title: 'Differential: fatigue', daysAgo: 5, first: 'What is the differential for fatigue with elevated A1C?', reply: 2 },
    { title: 'Note draft', daysAgo: 6, first: 'Draft a follow-up note from my dictation.', reply: 3 },
    { title: 'Diabetes follow-up plan', daysAgo: 9, first: 'What is the plan for the diabetes follow-up?', reply: 0 },
  ]
  return samples.map((s, idx) => {
    const created = new Date(Date.now() - s.daysAgo * 24 * 60 * 60 * 1000)
    const userMsg: ChatMessage = {
      id: makeId('m'),
      role: 'user',
      text: s.first,
      createdAt: created.toISOString(),
    }
    const replyText = CANNED_REPLIES[s.reply] ?? CANNED_REPLIES[0] ?? ''
    const cites = CANNED_CITATIONS[s.reply] ?? CANNED_CITATIONS[0] ?? []
    const assistantMsg: ChatMessage = {
      id: makeId('m'),
      role: 'assistant',
      text: replyText,
      createdAt: new Date(created.getTime() + 4_000).toISOString(),
      citations: cites,
    }
    return {
      id: `seed-${idx}`,
      createdAt: created.toISOString(),
      title: s.title,
      messages: [userMsg, assistantMsg],
    }
  })
}

export const useAgentForgeStore = defineStore('agentforge', () => {
  const conversations = ref<Conversation[]>([])
  const activeConversationId = ref<string | null>(null)
  const pendingAssistantText = ref<string | null>(null)
  const isStreaming = ref<boolean>(false)
  const hydrated = ref<boolean>(false)
  let replyCounter = 0

  function hydrate(): void {
    if (hydrated.value) return
    hydrated.value = true
    const persisted = readPersisted()
    if (persisted !== null && persisted.conversations.length > 0) {
      conversations.value = persisted.conversations.map((c) => ({
        id: c.id,
        createdAt: c.createdAt,
        title: c.title,
        messages: [...c.messages],
      }))
      activeConversationId.value = persisted.activeConversationId
    } else {
      conversations.value = buildSeedHistory()
      activeConversationId.value = null
    }
  }

  function persist(): void {
    if (typeof localStorage === 'undefined') return
    const payload: PersistedShape = {
      version: 1,
      conversations: conversations.value,
      activeConversationId: activeConversationId.value,
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    } catch {
      // Quota or serialization issue — drop silently; in-memory state is canonical.
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
    return created
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
    pendingAssistantText.value = null
    isStreaming.value = false
    persist()
  }

  function selectConversation(id: string): void {
    if (!hydrated.value) hydrate()
    if (!conversations.value.some((c) => c.id === id)) return
    activeConversationId.value = id
    pendingAssistantText.value = null
    isStreaming.value = false
    persist()
  }

  function tokenize(text: string): readonly string[] {
    // Cheap word-level tokenisation that preserves spacing for the typewriter.
    const parts = text.split(/(\s+)/)
    return parts.filter((p) => p.length > 0)
  }

  async function streamReply(reply: string, citations: readonly Citation[]): Promise<void> {
    const conv = activeConversation.value
    if (conv === null) return

    const tokens = tokenize(reply)
    pendingAssistantText.value = ''
    isStreaming.value = true

    for (const tok of tokens) {
      const delay = 50 + Math.floor(Math.random() * 50)
      await new Promise<void>((resolve) => setTimeout(resolve, delay))
      pendingAssistantText.value = (pendingAssistantText.value ?? '') + tok
    }

    const assistantMsg: ChatMessage = {
      id: makeId('m'),
      role: 'assistant',
      text: reply,
      createdAt: nowIso(),
      citations,
    }
    conv.messages = [...conv.messages, assistantMsg]
    pendingAssistantText.value = null
    isStreaming.value = false
    persist()
  }

  async function sendMessage(text: string): Promise<void> {
    const trimmed = text.trim()
    if (trimmed.length === 0) return
    if (isStreaming.value) return

    const conv = ensureConversation()

    const userMsg: ChatMessage = {
      id: makeId('m'),
      role: 'user',
      text: trimmed,
      createdAt: nowIso(),
    }
    conv.messages = [...conv.messages, userMsg]
    if (conv.title === 'New conversation') {
      conv.title = trimmed.length > 48 ? `${trimmed.slice(0, 45)}...` : trimmed
    }
    persist()

    const replyIdx = replyCounter % CANNED_REPLIES.length
    replyCounter += 1
    const reply = CANNED_REPLIES[replyIdx] ?? CANNED_REPLIES[0] ?? ''
    const citations = CANNED_CITATIONS[replyIdx] ?? CANNED_CITATIONS[0] ?? []

    await streamReply(reply, citations)
  }

  return {
    conversations,
    sortedConversations,
    activeConversationId,
    activeConversation,
    messages,
    pendingAssistantText,
    isStreaming,
    hydrate,
    newConversation,
    selectConversation,
    sendMessage,
  }
})
