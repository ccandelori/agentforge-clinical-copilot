<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import BaseButton from '@/components/ui/BaseButton.vue'
import { formatDateLong, formatMonthLong, startOfWeek, endOfWeek } from '@/lib/dates'
import {
  FACILITIES,
  PROVIDERS,
  useCalendarStore,
  type CalendarViewMode,
} from '@/stores/calendar'

const store = useCalendarStore()

const emit = defineEmits<{
  (e: 'new-appointment'): void
}>()

const providerOpen = ref<boolean>(false)
const providerRef = ref<HTMLDivElement | null>(null)

function toggleProviderMenu(): void {
  providerOpen.value = !providerOpen.value
}

function closeProviderMenu(): void {
  providerOpen.value = false
}

function onDocClick(ev: MouseEvent): void {
  if (!providerRef.value) return
  if (!providerRef.value.contains(ev.target as Node)) closeProviderMenu()
}

onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))

const VIEWS: ReadonlyArray<{ value: CalendarViewMode, label: string }> = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
]

const headerLabel = computed<string>(() => {
  const d = store.focusedDate
  if (store.viewMode === 'day') return formatDateLong(d)
  if (store.viewMode === 'month') return formatMonthLong(d)
  // Week
  const start = startOfWeek(d)
  const end = endOfWeek(d)
  const startLabel = start.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  })
  const endLabel = end.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  return `${startLabel} – ${endLabel}`
})

const providerSummary = computed<string>(() => {
  const set = store.providerFilter
  if (set.size === 0) return 'All providers'
  if (set.size === 1) return Array.from(set)[0] ?? 'All providers'
  return `${set.size} providers`
})
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface px-4 py-3 shadow-card">
    <div class="flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-1">
        <BaseButton variant="secondary" size="sm" @click="store.goToday()">
          Today
        </BaseButton>
        <button
          type="button"
          class="ml-1 inline-flex h-8 w-8 items-center justify-center rounded-md border border-line text-ink hover:bg-surface-2"
          aria-label="Previous"
          @click="store.goPrev()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </button>
        <button
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line text-ink hover:bg-surface-2"
          aria-label="Next"
          @click="store.goNext()"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
            <path d="m9 6 6 6-6 6" />
          </svg>
        </button>
      </div>
      <h1 class="text-lg font-semibold tracking-tight text-ink">
        {{ headerLabel }}
      </h1>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <!-- View toggle -->
      <div role="tablist" class="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
        <button
          v-for="v in VIEWS"
          :key="v.value"
          role="tab"
          type="button"
          class="rounded-md px-3 py-1 text-xs font-medium transition"
          :class="store.viewMode === v.value
            ? 'bg-surface text-ink shadow-card'
            : 'text-ink-muted hover:text-ink'"
          :aria-selected="store.viewMode === v.value"
          @click="store.setView(v.value)"
        >
          {{ v.label }}
        </button>
      </div>

      <!-- Provider multi-select -->
      <div ref="providerRef" class="relative">
        <button
          type="button"
          class="inline-flex h-8 items-center gap-2 rounded-md border border-line bg-surface px-3 text-xs font-medium text-ink hover:bg-surface-2"
          @click="toggleProviderMenu"
        >
          <span class="text-ink-muted">Providers:</span>
          <span>{{ providerSummary }}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3 w-3 text-ink-muted">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <div
          v-if="providerOpen"
          class="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-line bg-surface p-1 shadow-card-lg"
        >
          <ul class="max-h-72 overflow-auto py-1">
            <li v-for="p in PROVIDERS" :key="p">
              <label class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-surface-2">
                <input
                  type="checkbox"
                  class="h-3.5 w-3.5 accent-primary-600"
                  :checked="store.providerFilter.has(p)"
                  @change="store.toggleProvider(p)"
                />
                <span class="text-ink">{{ p }}</span>
              </label>
            </li>
          </ul>
        </div>
      </div>

      <!-- Facility -->
      <label class="sr-only" for="facility-select">Facility</label>
      <select
        id="facility-select"
        class="h-8 rounded-md border border-line bg-surface px-2 text-xs font-medium text-ink hover:bg-surface-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        :value="store.facilityFilter"
        @change="store.setFacility(($event.target as HTMLSelectElement).value)"
      >
        <option value="">All facilities</option>
        <option v-for="f in FACILITIES" :key="f" :value="f">{{ f }}</option>
      </select>

      <BaseButton size="sm" variant="primary" @click="emit('new-appointment')">
        + New appointment
      </BaseButton>
    </div>
  </div>
</template>
