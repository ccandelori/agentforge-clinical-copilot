<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { Appointment } from '@/api/mock'
import AppointmentDetailModal from '@/components/calendar/AppointmentDetailModal.vue'
import CalendarToolbar from '@/components/calendar/CalendarToolbar.vue'
import DayView from '@/components/calendar/DayView.vue'
import MonthView from '@/components/calendar/MonthView.vue'
import NewAppointmentModal from '@/components/calendar/NewAppointmentModal.vue'
import WeekView from '@/components/calendar/WeekView.vue'
import BaseSpinner from '@/components/ui/BaseSpinner.vue'
import { useCalendarStore } from '@/stores/calendar'

const store = useCalendarStore()

const newOpen = ref<boolean>(false)
const newInitialStart = ref<Date | null>(null)
const newInitialProvider = ref<string>('')

const detailOpen = ref<boolean>(false)
const detailAppointment = ref<Appointment | null>(null)

async function reload(): Promise<void> {
  switch (store.viewMode) {
    case 'day':
      await store.loadDay(store.focusedDate)
      return
    case 'week':
      await store.loadWeek(store.focusedDate)
      return
    case 'month':
      await store.loadMonth(store.focusedDate)
      return
  }
}

watch(
  () => [store.viewMode, store.focusedDate.getTime()] as const,
  () => {
    void reload()
  },
  { immediate: true },
)

function openNew(start: Date | null = null, provider: string = ''): void {
  newInitialStart.value = start ?? new Date(store.focusedDate.getTime() + 9 * 60 * 60 * 1000)
  newInitialProvider.value = provider
  newOpen.value = true
}

function openDetail(appt: Appointment): void {
  detailAppointment.value = appt
  detailOpen.value = true
}

function onSelectDay(d: Date): void {
  // Drilling down from month → day on click. If already on day view we
  // re-focus; otherwise switch.
  store.setFocusedDate(d)
  store.setView('day')
}

const visibleAppointmentCount = computed<number>(() => {
  if (store.viewMode === 'day') return store.appointmentsForDay(store.focusedDate).length
  // For week/month we don't bother counting precisely — `filteredAppointments`
  // is fine as a coarse summary.
  return store.filteredAppointments.length
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-3 p-4">
    <CalendarToolbar @new-appointment="openNew(null)" />

    <div class="flex items-center justify-between text-xs text-ink-muted">
      <span v-if="store.loading" class="inline-flex items-center gap-2">
        <BaseSpinner size="sm" /> Loading appointments…
      </span>
      <span v-else-if="store.error" class="text-danger-600">{{ store.error }}</span>
      <span v-else>{{ visibleAppointmentCount }} appointments visible</span>
      <span class="hidden sm:inline">Click an empty slot to schedule · Click an appointment to view details</span>
    </div>

    <div class="min-h-0 flex-1">
      <DayView
        v-if="store.viewMode === 'day'"
        :date="store.focusedDate"
        @select-slot="(s, p) => openNew(s, p)"
        @select-appointment="openDetail"
      />
      <WeekView
        v-else-if="store.viewMode === 'week'"
        :date="store.focusedDate"
        @select-slot="(s) => openNew(s)"
        @select-appointment="openDetail"
      />
      <MonthView
        v-else
        :date="store.focusedDate"
        @select-day="onSelectDay"
        @select-appointment="openDetail"
      />
    </div>

    <NewAppointmentModal
      :open="newOpen"
      :initial-start="newInitialStart"
      :initial-provider="newInitialProvider"
      @update:open="newOpen = $event"
    />

    <AppointmentDetailModal
      :open="detailOpen"
      :appointment="detailAppointment"
      @update:open="detailOpen = $event"
    />
  </div>
</template>
