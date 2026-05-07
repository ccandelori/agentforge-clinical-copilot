import { reactive, ref, watch, type Ref } from 'vue'

/**
 * Encounter draft state shape. Persisted to localStorage as JSON.
 * Stays plain & serialisable on purpose — anything fancier (Date,
 * Map, Set) breaks `JSON.parse` round-trips.
 */

export type PlanMedAction = 'start' | 'stop' | 'continue'

export interface PlanMedItem {
  readonly id: string
  name: string
  action: PlanMedAction
}

export interface AssessmentItem {
  readonly id: string
  icd10: string
  description: string
}

export interface PendingOrder {
  readonly id: string
  label: string
  detail: string
}

export interface AttachmentItem {
  readonly id: string
  filename: string
  sizeKb: number
  uploadedAt: string
}

export interface EncounterVitalsInput {
  heartRate: string
  systolic: string
  diastolic: string
  tempC: string
  respRate: string
  spo2: string
  weightKg: string
  heightCm: string
}

export interface EncounterDraft {
  encounterId: string
  // Subjective
  chiefComplaint: string
  hpi: string
  // Objective
  vitals: EncounterVitalsInput
  examHeart: string
  examLungs: string
  examAbdomen: string
  examNeuro: string
  // Assessment
  problems: AssessmentItem[]
  // Plan
  plannedMeds: PlanMedItem[]
  plannedLabs: string[]
  referrals: string
  followUpDate: string
  followUpNotes: string
  // Orders
  pendingOrders: PendingOrder[]
  // Attachments
  attachments: AttachmentItem[]
  // Lifecycle
  lastSavedAt: string | null
  signedAt: string | null
}

export interface UseEncounterDraftReturn {
  draft: EncounterDraft
  lastSavedAt: Ref<string | null>
  signedAt: Ref<string | null>
  isDirty: Ref<boolean>
  saveNow: () => void
  finalize: () => void
  reset: () => void
}

const STORAGE_PREFIX = 'encounter-draft.'
const SAVE_DEBOUNCE_MS = 600

function storageKey(id: string): string {
  return `${STORAGE_PREFIX}${id}`
}

function defaultDraft(id: string): EncounterDraft {
  return {
    encounterId: id,
    chiefComplaint: '',
    hpi: '',
    vitals: {
      heartRate: '',
      systolic: '',
      diastolic: '',
      tempC: '',
      respRate: '',
      spo2: '',
      weightKg: '',
      heightCm: '',
    },
    examHeart: '',
    examLungs: '',
    examAbdomen: '',
    examNeuro: '',
    problems: [],
    plannedMeds: [],
    plannedLabs: [],
    referrals: '',
    followUpDate: '',
    followUpNotes: '',
    pendingOrders: [
      { id: 'ord-1', label: 'CBC w/ diff', detail: 'Routine, fasting not required' },
      { id: 'ord-2', label: 'Lipid panel', detail: 'Fasting 10h preferred' },
      { id: 'ord-3', label: 'EKG 12-lead', detail: 'Office, today' },
    ],
    attachments: [
      { id: 'att-1', filename: 'prior-imaging.pdf', sizeKb: 412, uploadedAt: '2026-04-22T14:18:00Z' },
      { id: 'att-2', filename: 'patient-photo.jpg', sizeKb: 88, uploadedAt: '2026-04-22T14:19:00Z' },
    ],
    lastSavedAt: null,
    signedAt: null,
  }
}

function readFromStorage(id: string): EncounterDraft | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(storageKey(id))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    // Merge over defaults so newly added fields don't break old drafts.
    const base = defaultDraft(id)
    return { ...base, ...(parsed as Partial<EncounterDraft>) }
  } catch {
    return null
  }
}

function writeToStorage(id: string, draft: EncounterDraft): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(storageKey(id), JSON.stringify(draft))
  } catch {
    // Quota exceeded / disabled storage — ignore silently.
  }
}

export function useEncounterDraft(id: string): UseEncounterDraftReturn {
  const initial = readFromStorage(id) ?? defaultDraft(id)
  const draft = reactive<EncounterDraft>(initial)
  const lastSavedAt = ref<string | null>(initial.lastSavedAt)
  const signedAt = ref<string | null>(initial.signedAt)
  const isDirty = ref<boolean>(false)

  let saveTimer: ReturnType<typeof setTimeout> | null = null

  function commitSave(): void {
    draft.lastSavedAt = new Date().toISOString()
    lastSavedAt.value = draft.lastSavedAt
    writeToStorage(id, draft)
    isDirty.value = false
  }

  function saveNow(): void {
    if (saveTimer) {
      clearTimeout(saveTimer)
      saveTimer = null
    }
    commitSave()
  }

  function finalize(): void {
    draft.signedAt = new Date().toISOString()
    signedAt.value = draft.signedAt
    saveNow()
  }

  function reset(): void {
    const fresh = defaultDraft(id)
    Object.assign(draft, fresh)
    lastSavedAt.value = null
    signedAt.value = null
    isDirty.value = false
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem(storageKey(id))
    }
  }

  // Auto-save with debounce on any change.
  watch(
    () => draft,
    () => {
      if (signedAt.value) return // Don't keep mutating after sign-off.
      isDirty.value = true
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = setTimeout(() => {
        commitSave()
        saveTimer = null
      }, SAVE_DEBOUNCE_MS)
    },
    { deep: true },
  )

  return { draft, lastSavedAt, signedAt, isDirty, saveNow, finalize, reset }
}
