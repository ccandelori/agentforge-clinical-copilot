<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSpinner from '@/components/ui/BaseSpinner.vue'
import PatientDensityToggle, {
  type Density,
} from '@/components/patients/list/PatientDensityToggle.vue'
import PatientFilterBar, {
  type PatientFilters,
} from '@/components/patients/list/PatientFilterBar.vue'
import PatientRow from '@/components/patients/list/PatientRow.vue'

import { useDebouncedRef } from '@/composables/useDebouncedRef'
import { listPatients, type Patient } from '@/api/mock'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DENSITY_STORAGE_KEY = 'patients.density'
const PAGE_SIZE = 25

type SortKey = 'name' | 'dob' | 'mrn' | 'lastVisit'
type SortDir = 'asc' | 'desc'

interface PatientView extends Record<string, unknown> {
  readonly patient: Patient
  readonly id: string
  readonly fullName: string
  readonly mrn: string
  readonly dob: string
  readonly age: number
  readonly lastVisit: string | null
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ageFromDob(dob: string): number {
  const birth = new Date(dob)
  if (Number.isNaN(birth.getTime())) return 0
  const now = new Date()
  let age = now.getUTCFullYear() - birth.getUTCFullYear()
  const m = now.getUTCMonth() - birth.getUTCMonth()
  if (m < 0 || (m === 0 && now.getUTCDate() < birth.getUTCDate())) {
    age -= 1
  }
  return age
}

/**
 * Deterministic mock "last visit" timestamp derived from the patient id so
 * the column has stable, sortable values without requiring an extra fetch.
 */
function fakeLastVisit(p: Patient): string | null {
  const tail = p.id.slice(-2)
  const n = Number.parseInt(tail, 10)
  if (Number.isNaN(n)) return null
  if (n % 7 === 0) return null // some patients have no recorded visit
  const days = (n * 11) % 365
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString()
}

function readDensity(): Density {
  if (typeof localStorage === 'undefined') return 'comfortable'
  const v = localStorage.getItem(DENSITY_STORAGE_KEY)
  return v === 'compact' ? 'compact' : 'comfortable'
}

function ageBandMatches(age: number, band: PatientFilters['ageBand']): boolean {
  switch (band) {
    case 'all':
      return true
    case '0-17':
      return age <= 17
    case '18-64':
      return age >= 18 && age <= 64
    case '65+':
      return age >= 65
  }
}

/**
 * The mock API has no notion of active/inactive — derive a stable proxy
 * from the patient id so the filter does something visible.
 */
function isActive(p: Patient): boolean {
  const tail = p.id.slice(-1)
  const n = Number.parseInt(tail, 10)
  return Number.isNaN(n) ? true : n % 4 !== 0
}

function compareStrings(a: string, b: string): number {
  return a.localeCompare(b, undefined, { sensitivity: 'base' })
}

function compareNullableIso(a: string | null, b: string | null): number {
  // Nulls sort last regardless of direction.
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return a.localeCompare(b)
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const router = useRouter()

const allPatients = shallowRef<readonly Patient[]>([])
const initialLoading = ref<boolean>(true)
const refetching = ref<boolean>(false)
const fetchError = ref<string>('')

const searchInput = ref<string>('')
const debouncedSearch = useDebouncedRef<string>('', 250)

const filters = ref<PatientFilters>({
  gender: 'all',
  ageBand: 'all',
  status: 'all',
})

const density = ref<Density>(readDensity())
const sortKey = ref<SortKey>('name')
const sortDir = ref<SortDir>('asc')
const page = ref<number>(1)
const jumpInput = ref<string>('1')

// Persist density to localStorage.
watch(density, (next) => {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(DENSITY_STORAGE_KEY, next)
  }
})

// Mirror the immediate input into the debounced ref.
watch(searchInput, (next) => {
  debouncedSearch.value = next
})

// Reset to first page whenever the result set shape changes.
watch([debouncedSearch, filters, sortKey, sortDir], () => {
  page.value = 1
})

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function load(query: string): Promise<void> {
  fetchError.value = ''
  if (allPatients.value.length === 0) {
    initialLoading.value = true
  } else {
    refetching.value = true
  }
  try {
    const result = await listPatients(query)
    allPatients.value = result
  } catch (e) {
    fetchError.value = e instanceof Error ? e.message : 'Failed to load patients'
  } finally {
    initialLoading.value = false
    refetching.value = false
  }
}

watch(
  debouncedSearch,
  (q) => {
    void load(q)
  },
  { immediate: false },
)

onMounted(() => {
  void load('')
})

function refresh(): void {
  void load(debouncedSearch.value)
}

function clearSearch(): void {
  searchInput.value = ''
  debouncedSearch.value = ''
}

// ---------------------------------------------------------------------------
// Derived rows
// ---------------------------------------------------------------------------

const enrichedRows = computed<readonly PatientView[]>(() =>
  allPatients.value.map((p) => ({
    patient: p,
    id: p.id,
    fullName: `${p.firstName} ${p.lastName}`,
    mrn: p.mrn,
    dob: p.dob,
    age: ageFromDob(p.dob),
    lastVisit: fakeLastVisit(p),
  })),
)

const filteredRows = computed<readonly PatientView[]>(() => {
  const f = filters.value
  return enrichedRows.value.filter((row) => {
    if (f.gender !== 'all' && row.patient.sex !== f.gender) return false
    if (!ageBandMatches(row.age, f.ageBand)) return false
    if (f.status === 'active' && !isActive(row.patient)) return false
    if (f.status === 'inactive' && isActive(row.patient)) return false
    return true
  })
})

const sortedRows = computed<readonly PatientView[]>(() => {
  const list = [...filteredRows.value]
  const dir = sortDir.value === 'asc' ? 1 : -1
  list.sort((a, b) => {
    switch (sortKey.value) {
      case 'name':
        return compareStrings(a.fullName, b.fullName) * dir
      case 'dob':
        return compareStrings(a.dob, b.dob) * dir
      case 'mrn':
        return compareStrings(a.mrn, b.mrn) * dir
      case 'lastVisit':
        return compareNullableIso(a.lastVisit, b.lastVisit) * dir
    }
  })
  return list
})

const totalCount = computed<number>(() => sortedRows.value.length)

const pageCount = computed<number>(() =>
  Math.max(1, Math.ceil(totalCount.value / PAGE_SIZE)),
)

const pagedRows = computed<readonly PatientView[]>(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return sortedRows.value.slice(start, start + PAGE_SIZE)
})

watch(page, (next) => {
  jumpInput.value = String(next)
})

watch(pageCount, (count) => {
  if (page.value > count) page.value = count
})

// ---------------------------------------------------------------------------
// Sorting / paging interactions
// ---------------------------------------------------------------------------

function onSortBy(key: SortKey): void {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'asc'
  }
}

function sortIndicator(key: SortKey): string {
  if (sortKey.value !== key) return ''
  return sortDir.value === 'asc' ? '▲' : '▼'
}

function ariaSort(key: SortKey): 'ascending' | 'descending' | 'none' {
  if (sortKey.value !== key) return 'none'
  return sortDir.value === 'asc' ? 'ascending' : 'descending'
}

function gotoPrev(): void {
  if (page.value > 1) page.value -= 1
}

function gotoNext(): void {
  if (page.value < pageCount.value) page.value += 1
}

function onJumpSubmit(): void {
  const next = Number.parseInt(jumpInput.value, 10)
  if (Number.isNaN(next)) {
    jumpInput.value = String(page.value)
    return
  }
  const clamped = Math.max(1, Math.min(pageCount.value, next))
  page.value = clamped
  jumpInput.value = String(clamped)
}

function onRowClick(row: PatientView): void {
  void router.push({ name: 'patient-dashboard', params: { id: row.id } })
}

function onAddPatient(): void {
  // No-op per spec.
}

// ---------------------------------------------------------------------------
// Table column config
// ---------------------------------------------------------------------------

const COLUMN_COUNT = 6

// Skeleton row count for first-fetch placeholder.
const SKELETON_ROWS = 8

// ---------------------------------------------------------------------------
// Cell-row interaction
// ---------------------------------------------------------------------------

function onOpenActions(_patient: Patient): void {
  // Stub: actions menu wiring is out of scope for Wave 2b.
}
</script>

<template>
  <div class="flex flex-col gap-5">
    <!-- Page header -->
    <header class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <h1 class="text-2xl font-semibold text-ink">Patients</h1>
        <BaseBadge variant="neutral">{{ totalCount }}</BaseBadge>
      </div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          aria-label="Refresh"
          :disabled="refetching || initialLoading"
          @click="refresh"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            class="h-4 w-4"
            :class="{ 'animate-spin': refetching }"
            aria-hidden="true"
          >
            <path
              fill-rule="evenodd"
              d="M15.312 11.424a5.5 5.5 0 0 1-9.201 2.466l-.312-.311h2.433a.75.75 0 0 0 0-1.5H3.989a.75.75 0 0 0-.75.75v4.242a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.712-3.138.75.75 0 0 0-1.449-.39Zm1.23-3.723a.75.75 0 0 0 .219-.53V2.929a.75.75 0 0 0-1.5 0V5.36l-.31-.31A7 7 0 0 0 3.239 8.188a.75.75 0 1 0 1.449.39A5.5 5.5 0 0 1 13.89 6.11l.311.31h-2.432a.75.75 0 0 0 0 1.5h4.243a.75.75 0 0 0 .53-.219Z"
              clip-rule="evenodd"
            />
          </svg>
        </button>
        <BaseButton variant="primary" @click="onAddPatient">
          <span aria-hidden="true">+</span>
          Add patient
        </BaseButton>
      </div>
    </header>

    <!-- Search + density -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0 flex-1 max-w-md">
        <BaseInput
          v-model="searchInput"
          placeholder="Search by name or MRN…"
        >
          <template #prefix>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              class="h-4 w-4"
              aria-hidden="true"
            >
              <path
                fill-rule="evenodd"
                d="M9 3.5a5.5 5.5 0 1 0 3.473 9.78l3.124 3.123a.75.75 0 1 0 1.06-1.06l-3.123-3.124A5.5 5.5 0 0 0 9 3.5ZM5 9a4 4 0 1 1 8 0 4 4 0 0 1-8 0Z"
                clip-rule="evenodd"
              />
            </svg>
          </template>
          <template v-if="searchInput" #suffix>
            <button
              type="button"
              class="rounded p-1 text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              aria-label="Clear search"
              @click="clearSearch"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 20 20"
                fill="currentColor"
                class="h-4 w-4"
                aria-hidden="true"
              >
                <path
                  d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
                />
              </svg>
            </button>
          </template>
        </BaseInput>
      </div>
      <PatientDensityToggle v-model="density" />
    </div>

    <!-- Filters -->
    <PatientFilterBar v-model="filters" />

    <!-- Error banner -->
    <div
      v-if="fetchError"
      role="alert"
      class="rounded-lg border border-danger-500 bg-danger-50 px-4 py-2 text-sm text-danger-700"
    >
      {{ fetchError }}
    </div>

    <!-- Table region -->
    <div class="relative">
      <!-- First-fetch skeleton -->
      <div
        v-if="initialLoading"
        class="overflow-hidden rounded-xl border border-line"
        aria-busy="true"
        aria-live="polite"
      >
        <div class="bg-surface-2 px-4 py-2 text-xs uppercase tracking-wide text-ink-muted">
          Loading patients…
        </div>
        <div class="divide-y divide-line bg-surface">
          <div
            v-for="n in SKELETON_ROWS"
            :key="`sk-${n}`"
            class="flex items-center gap-3 px-4 py-3"
          >
            <div class="h-9 w-9 animate-pulse rounded-full bg-surface-2" />
            <div class="flex-1 space-y-1.5">
              <div class="h-3 w-1/3 animate-pulse rounded bg-surface-2" />
              <div class="h-2.5 w-1/4 animate-pulse rounded bg-surface-2" />
            </div>
            <div class="h-3 w-20 animate-pulse rounded bg-surface-2" />
            <div class="h-3 w-16 animate-pulse rounded bg-surface-2" />
            <div class="h-3 w-12 animate-pulse rounded bg-surface-2" />
          </div>
        </div>
      </div>

      <template v-else>
        <!-- Mirrors BaseTable's structure/styling but adds clickable
             sortable headers (BaseTable doesn't emit header events). -->
        <div class="overflow-hidden rounded-xl border border-line">
          <table class="w-full text-sm">
            <thead class="bg-surface-2 text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th
                  scope="col"
                  class="cursor-pointer select-none px-4 py-2 text-left font-semibold hover:text-ink"
                  :aria-sort="ariaSort('name')"
                  @click="onSortBy('name')"
                >
                  Name <span class="ml-1">{{ sortIndicator('name') }}</span>
                </th>
                <th
                  scope="col"
                  class="cursor-pointer select-none px-4 py-2 text-left font-semibold hover:text-ink"
                  style="width: 140px"
                  :aria-sort="ariaSort('mrn')"
                  @click="onSortBy('mrn')"
                >
                  MRN <span class="ml-1">{{ sortIndicator('mrn') }}</span>
                </th>
                <th
                  scope="col"
                  class="cursor-pointer select-none px-4 py-2 text-left font-semibold hover:text-ink"
                  style="width: 160px"
                  :aria-sort="ariaSort('dob')"
                  @click="onSortBy('dob')"
                >
                  DOB / Age <span class="ml-1">{{ sortIndicator('dob') }}</span>
                </th>
                <th
                  scope="col"
                  class="px-4 py-2 text-left font-semibold"
                  style="width: 120px"
                >
                  Gender
                </th>
                <th
                  scope="col"
                  class="cursor-pointer select-none px-4 py-2 text-left font-semibold hover:text-ink"
                  style="width: 160px"
                  :aria-sort="ariaSort('lastVisit')"
                  @click="onSortBy('lastVisit')"
                >
                  Last visit
                  <span class="ml-1">{{ sortIndicator('lastVisit') }}</span>
                </th>
                <th
                  scope="col"
                  class="px-4 py-2 text-right font-semibold"
                  style="width: 60px"
                >
                  <span class="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line bg-surface">
              <tr v-if="pagedRows.length === 0">
                <td :colspan="COLUMN_COUNT" class="px-4 py-10">
                  <BaseEmptyState
                    title="No patients match"
                    message="Try clearing search or adjusting filters."
                    icon="🔍"
                  />
                </td>
              </tr>
              <tr
                v-for="row in pagedRows"
                v-else
                :key="row.id"
                class="cursor-pointer hover:bg-surface-2"
                :class="density === 'compact' ? '[&>td]:py-1.5' : '[&>td]:py-3'"
                @click="onRowClick(row)"
              >
                <td class="px-4 align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="name"
                    :density="density"
                    :last-visit="row.lastVisit"
                  />
                </td>
                <td class="px-4 align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="mrn"
                    :density="density"
                  />
                </td>
                <td class="px-4 align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="dob"
                    :density="density"
                  />
                </td>
                <td class="px-4 align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="sex"
                    :density="density"
                  />
                </td>
                <td class="px-4 align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="lastVisit"
                    :last-visit="row.lastVisit"
                    :density="density"
                  />
                </td>
                <td class="px-4 text-right align-middle">
                  <PatientRow
                    :patient="row.patient"
                    field="actions"
                    :density="density"
                    @open-actions="onOpenActions"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <!-- Refetch overlay -->
      <div
        v-if="refetching && !initialLoading"
        class="pointer-events-none absolute inset-0 flex items-start justify-center pt-6"
        aria-hidden="true"
      >
        <div
          class="rounded-full bg-surface px-3 py-1.5 text-xs text-ink-muted shadow-card"
        >
          <BaseSpinner size="sm" />
          <span class="ml-2 align-middle">Updating…</span>
        </div>
      </div>
    </div>

    <!-- Pagination -->
    <nav
      v-if="totalCount > 0"
      class="flex flex-wrap items-center justify-between gap-3"
      aria-label="Patient list pagination"
    >
      <p class="text-xs text-ink-muted">
        Showing
        <span class="font-medium text-ink">
          {{ (page - 1) * PAGE_SIZE + 1 }}
        </span>
        –
        <span class="font-medium text-ink">
          {{ Math.min(page * PAGE_SIZE, totalCount) }}
        </span>
        of
        <span class="font-medium text-ink">{{ totalCount }}</span>
      </p>
      <div class="flex items-center gap-2">
        <BaseButton
          variant="secondary"
          size="sm"
          :disabled="page === 1"
          @click="gotoPrev"
        >
          Prev
        </BaseButton>
        <span class="text-xs text-ink-muted">
          Page <span class="font-medium text-ink">{{ page }}</span>
          of {{ pageCount }}
        </span>
        <BaseButton
          variant="secondary"
          size="sm"
          :disabled="page === pageCount"
          @click="gotoNext"
        >
          Next
        </BaseButton>
        <form class="flex items-center gap-1" @submit.prevent="onJumpSubmit">
          <label for="patients-jump" class="text-xs text-ink-muted">
            Jump to
          </label>
          <input
            id="patients-jump"
            v-model="jumpInput"
            type="number"
            min="1"
            :max="pageCount"
            class="w-16 rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
          />
          <BaseButton variant="ghost" size="sm" type="submit">Go</BaseButton>
        </form>
      </div>
    </nav>
  </div>
</template>
