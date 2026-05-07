import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { getAppointments, type Appointment, type AppointmentStatus } from '@/api/mock'
import {
  addDays,
  dayKey,
  endOfMonth,
  endOfWeek,
  startOfDay,
  startOfMonth,
  startOfWeek,
} from '@/lib/dates'

/**
 * Calendar/Scheduler store (Wave 2d).
 *
 * Holds the current view, the focused date, the active provider/facility
 * filters, and a per-day cache of appointments. Loading actions build a
 * day-keyed cache so re-navigation feels instant.
 *
 * Wave 3 will swap `getAppointments` for a real FHIR call; this store's
 * surface should stay stable.
 */

export type CalendarViewMode = 'day' | 'week' | 'month'

export interface NewAppointmentInput {
  readonly patientId: string
  readonly patientName: string
  readonly providerName: string
  readonly facilityName: string
  readonly start: Date
  readonly durationMinutes: number
  readonly type: string
  readonly notes: string
}

/** Static list of providers used for filters and dropdowns. */
export const PROVIDERS: readonly string[] = [
  'Dr. Patel',
  'Dr. Lee',
  'Dr. Wong',
  'Dr. Garcia',
] as const

/** Static facility list. */
export const FACILITIES: readonly string[] = [
  'Main Clinic',
  'North Annex',
  'Telehealth',
] as const

export const APPOINTMENT_TYPES: readonly string[] = [
  'Follow-up',
  'New visit',
  'Annual physical',
  'Telehealth',
  'Procedure',
  'Lab review',
] as const

/**
 * Stable colour bucket per provider. Returns Tailwind utility fragments
 * (background, border, text) so views can compose them on their own
 * containers without trying to merge string literals.
 */
export interface ProviderPalette {
  readonly bg: string
  readonly border: string
  readonly text: string
  readonly dot: string
}

const PROVIDER_PALETTES: readonly ProviderPalette[] = [
  { bg: 'bg-primary-100', border: 'border-primary-500', text: 'text-primary-700', dot: 'bg-primary-500' },
  { bg: 'bg-info-100', border: 'border-info-500', text: 'text-info-700', dot: 'bg-info-500' },
  { bg: 'bg-warning-100', border: 'border-warning-500', text: 'text-warning-700', dot: 'bg-warning-500' },
  { bg: 'bg-success-100', border: 'border-success-500', text: 'text-success-700', dot: 'bg-success-500' },
  { bg: 'bg-danger-100', border: 'border-danger-500', text: 'text-danger-700', dot: 'bg-danger-500' },
]

export function paletteFor(providerName: string): ProviderPalette {
  // Stable hash from the name.
  let h = 0
  for (let i = 0; i < providerName.length; i += 1) {
    h = (h * 31 + providerName.charCodeAt(i)) >>> 0
  }
  return PROVIDER_PALETTES[h % PROVIDER_PALETTES.length] as ProviderPalette
}

const SYNTHETIC_PATIENTS: ReadonlyArray<readonly [string, string]> = [
  ['p-0001', 'Alvera Beahan'],
  ['p-0003', 'Marisol Reichel'],
  ['p-0005', 'Janelle Kovacek'],
  ['p-0007', 'Idella Kuvalis'],
  ['p-0009', 'Luella Hessel'],
  ['p-0011', 'Stephania Wuckert'],
]

const SYNTHETIC_REASONS: readonly string[] = [
  'Med refill',
  'BP check',
  'Diabetes follow-up',
  'Annual physical',
  'Telehealth check-in',
  'Lab review',
]

const SYNTHETIC_STATUSES: readonly AppointmentStatus[] = ['booked', 'arrived', 'fulfilled', 'booked']

/**
 * Generate a small set of deterministic, visually-rich appointments for a
 * given calendar day. Helps fill out the week and month views beyond the
 * 8 mock-API rows.
 */
function buildSynthetic(date: Date): readonly Appointment[] {
  const base = startOfDay(date).getTime()
  // Use the date as a seed so each day looks different but stable.
  const seed = (date.getFullYear() * 10000 + (date.getMonth() + 1) * 100 + date.getDate()) >>> 0
  const count = 2 + (seed % 4) // 2..5 extras per day
  const out: Appointment[] = []
  for (let i = 0; i < count; i += 1) {
    const slotIdx = (seed + i * 17) % 22 // 22 30-min slots from 7am to 6pm
    const startMinutes = 7 * 60 + slotIdx * 30
    const start = new Date(base + startMinutes * 60_000)
    const dur = 30 + ((seed + i) % 2) * 30 // 30 or 60 min
    const end = new Date(start.getTime() + dur * 60_000)
    const patient = SYNTHETIC_PATIENTS[(seed + i) % SYNTHETIC_PATIENTS.length] as readonly [string, string]
    const provider = PROVIDERS[(seed + i * 3) % PROVIDERS.length] as string
    const reason = SYNTHETIC_REASONS[(seed + i * 5) % SYNTHETIC_REASONS.length] as string
    const status = SYNTHETIC_STATUSES[(seed + i) % SYNTHETIC_STATUSES.length] as AppointmentStatus
    out.push({
      id: `syn-${dayKey(date)}-${i}`,
      patientId: patient[0],
      patientName: patient[1],
      start: start.toISOString(),
      end: end.toISOString(),
      providerName: provider,
      reason,
      status,
    })
  }
  return out
}

export const useCalendarStore = defineStore('calendar', () => {
  const viewMode = ref<CalendarViewMode>('week')
  const focusedDate = ref<Date>(startOfDay(new Date()))
  /** Provider names that are *visible*. Empty set = show all. */
  const providerFilter = ref<ReadonlySet<string>>(new Set())
  const facilityFilter = ref<string>('') // '' = all facilities

  const cache = ref<Map<string, readonly Appointment[]>>(new Map())
  const userAppointments = ref<readonly Appointment[]>([])
  const loading = ref<boolean>(false)
  const error = ref<string | null>(null)

  function setView(next: CalendarViewMode): void {
    viewMode.value = next
  }

  function setFocusedDate(next: Date): void {
    focusedDate.value = startOfDay(next)
  }

  function goToday(): void {
    setFocusedDate(new Date())
  }

  function goPrev(): void {
    const step = viewMode.value === 'day' ? 1 : viewMode.value === 'week' ? 7 : 0
    if (step > 0) {
      setFocusedDate(addDays(focusedDate.value, -step))
      return
    }
    // Month: jump to first of previous month.
    const cur = focusedDate.value
    setFocusedDate(new Date(cur.getFullYear(), cur.getMonth() - 1, 1))
  }

  function goNext(): void {
    const step = viewMode.value === 'day' ? 1 : viewMode.value === 'week' ? 7 : 0
    if (step > 0) {
      setFocusedDate(addDays(focusedDate.value, step))
      return
    }
    const cur = focusedDate.value
    setFocusedDate(new Date(cur.getFullYear(), cur.getMonth() + 1, 1))
  }

  function toggleProvider(name: string): void {
    const next = new Set(providerFilter.value)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    providerFilter.value = next
  }

  function setFacility(name: string): void {
    facilityFilter.value = name
  }

  async function loadDay(date: Date): Promise<void> {
    const key = dayKey(date)
    if (cache.value.has(key)) return
    loading.value = true
    error.value = null
    try {
      const fromApi = await getAppointments(date.toISOString())
      const synthetic = buildSynthetic(date)
      const next = new Map(cache.value)
      next.set(key, [...fromApi, ...synthetic])
      cache.value = next
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load appointments'
    } finally {
      loading.value = false
    }
  }

  async function loadWeek(date: Date): Promise<void> {
    const start = startOfWeek(date)
    const end = endOfWeek(date)
    const days: Date[] = []
    for (let cur = start; cur.getTime() <= end.getTime(); cur = addDays(cur, 1)) {
      days.push(cur)
    }
    await Promise.all(days.map((d) => loadDay(d)))
  }

  async function loadMonth(date: Date): Promise<void> {
    const start = startOfWeek(startOfMonth(date)) // include leading days
    const end = endOfWeek(endOfMonth(date)) // include trailing days
    const days: Date[] = []
    for (let cur = start; cur.getTime() <= end.getTime(); cur = addDays(cur, 1)) {
      days.push(cur)
    }
    await Promise.all(days.map((d) => loadDay(d)))
  }

  /** All appointments currently in cache, post-filter. */
  const filteredAppointments = computed<readonly Appointment[]>(() => {
    const all: Appointment[] = []
    for (const list of cache.value.values()) all.push(...list)
    all.push(...userAppointments.value)
    const providers = providerFilter.value
    return all.filter((a) => {
      if (providers.size > 0 && !providers.has(a.providerName)) return false
      return true
    })
  })

  /** Return the appointments that overlap a single calendar day. */
  function appointmentsForDay(date: Date): readonly Appointment[] {
    const dayStart = startOfDay(date).getTime()
    const dayEnd = dayStart + 24 * 60 * 60 * 1000
    return filteredAppointments.value
      .filter((a) => {
        const start = new Date(a.start).getTime()
        return start >= dayStart && start < dayEnd
      })
      .slice()
      .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime())
  }

  function addAppointment(input: NewAppointmentInput): Appointment {
    const start = input.start
    const end = new Date(start.getTime() + input.durationMinutes * 60_000)
    const appt: Appointment = {
      id: `local-${Date.now()}-${Math.floor(Math.random() * 9999)}`,
      patientId: input.patientId,
      patientName: input.patientName,
      start: start.toISOString(),
      end: end.toISOString(),
      providerName: input.providerName,
      reason: input.type + (input.notes ? ` — ${input.notes}` : ''),
      status: 'booked',
    }
    userAppointments.value = [...userAppointments.value, appt]
    return appt
  }

  return {
    viewMode,
    focusedDate,
    providerFilter,
    facilityFilter,
    loading,
    error,
    filteredAppointments,
    appointmentsForDay,
    setView,
    setFocusedDate,
    goToday,
    goPrev,
    goNext,
    toggleProvider,
    setFacility,
    loadDay,
    loadWeek,
    loadMonth,
    addAppointment,
  }
})
