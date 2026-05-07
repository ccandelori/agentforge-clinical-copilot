<script setup lang="ts">
import { computed } from 'vue'

import type { Appointment } from '@/api/mock'
import AppointmentBlock from '@/components/calendar/AppointmentBlock.vue'
import {
  addDays,
  endOfMonth,
  endOfWeek,
  sameDay,
  startOfMonth,
  startOfWeek,
} from '@/lib/dates'
import { useCalendarStore } from '@/stores/calendar'

interface Props {
  date: Date
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select-day', day: Date): void
  (e: 'select-appointment', appointment: Appointment): void
}>()

const store = useCalendarStore()

const WEEK_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const

interface Cell {
  readonly date: Date
  readonly inMonth: boolean
  readonly isToday: boolean
}

const cells = computed<readonly Cell[]>(() => {
  const start = startOfWeek(startOfMonth(props.date))
  const end = endOfWeek(endOfMonth(props.date))
  const out: Cell[] = []
  const targetMonth = props.date.getMonth()
  const today = new Date()
  for (let cur = start; cur.getTime() <= end.getTime(); cur = addDays(cur, 1)) {
    out.push({
      date: cur,
      inMonth: cur.getMonth() === targetMonth,
      isToday: sameDay(cur, today),
    })
  }
  return out
})

const MAX_VISIBLE = 3

function appointmentsForCell(d: Date): readonly Appointment[] {
  return store.appointmentsForDay(d)
}
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
    <!-- Weekday headers -->
    <div class="grid grid-cols-7 border-b border-line bg-surface-2">
      <div
        v-for="day in WEEK_DAYS"
        :key="day"
        class="px-3 py-2 text-center text-[11px] font-semibold uppercase tracking-wide text-ink-muted"
      >
        {{ day }}
      </div>
    </div>

    <!-- Day cells -->
    <div class="grid flex-1 grid-cols-7 grid-rows-6 overflow-hidden">
      <div
        v-for="(cell, idx) in cells"
        :key="idx"
        class="group flex flex-col items-stretch border-b border-r border-line p-1.5 text-left text-xs transition"
        :class="[
          !cell.inMonth ? 'bg-surface-2/50 text-ink-muted' : 'bg-surface text-ink',
        ]"
      >
        <button
          type="button"
          class="flex w-full items-center justify-between rounded hover:bg-surface-2"
          @click="emit('select-day', cell.date)"
        >
          <span
            class="inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold"
            :class="cell.isToday ? 'bg-primary-600 text-white' : ''"
          >
            {{ cell.date.getDate() }}
          </span>
          <span
            v-if="appointmentsForCell(cell.date).length > 0"
            class="px-1 text-[10px] text-ink-muted"
          >
            {{ appointmentsForCell(cell.date).length }}
          </span>
        </button>

        <div class="mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-hidden">
          <AppointmentBlock
            v-for="appt in appointmentsForCell(cell.date).slice(0, MAX_VISIBLE)"
            :key="appt.id"
            :appointment="appt"
            compact
            @click="emit('select-appointment', appt)"
          />
          <button
            v-if="appointmentsForCell(cell.date).length > MAX_VISIBLE"
            type="button"
            class="px-1 text-left text-[10px] font-medium text-primary-600 hover:underline"
            @click="emit('select-day', cell.date)"
          >
            +{{ appointmentsForCell(cell.date).length - MAX_VISIBLE }} more
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
