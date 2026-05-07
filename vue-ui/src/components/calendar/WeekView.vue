<script setup lang="ts">
import { computed } from 'vue'

import type { Appointment } from '@/api/mock'
import AppointmentBlock from '@/components/calendar/AppointmentBlock.vue'
import { addDays, formatTime, sameDay, startOfDay, startOfWeek } from '@/lib/dates'
import { useCalendarStore } from '@/stores/calendar'

interface Props {
  date: Date
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select-slot', slotStart: Date): void
  (e: 'select-appointment', appointment: Appointment): void
}>()

const store = useCalendarStore()

const START_HOUR = 7
const END_HOUR = 19 // exclusive
const HOURS = END_HOUR - START_HOUR
const PX_PER_HOUR = 56
const PX_PER_MINUTE = PX_PER_HOUR / 60

const days = computed<readonly Date[]>(() => {
  const start = startOfWeek(props.date)
  const out: Date[] = []
  for (let i = 0; i < 7; i += 1) out.push(addDays(start, i))
  return out
})

const hourLabels = computed<readonly string[]>(() => {
  const out: string[] = []
  const base = startOfDay(props.date)
  for (let h = START_HOUR; h < END_HOUR; h += 1) {
    base.setHours(h, 0, 0, 0)
    out.push(formatTime(base))
  }
  return out
})

interface PositionedAppt {
  readonly appt: Appointment
  readonly topPx: number
  readonly heightPx: number
}

function appointmentsForDay(d: Date): readonly PositionedAppt[] {
  const list = store.appointmentsForDay(d)
  return list
    .map<PositionedAppt | null>((appt) => {
      const start = new Date(appt.start)
      const end = new Date(appt.end)
      const minutesFromGridStart = (start.getHours() - START_HOUR) * 60 + start.getMinutes()
      const dur = Math.max(15, (end.getTime() - start.getTime()) / 60_000)
      if (minutesFromGridStart < 0) return null
      if (minutesFromGridStart >= HOURS * 60) return null
      return {
        appt,
        topPx: minutesFromGridStart * PX_PER_MINUTE,
        heightPx: Math.min(dur * PX_PER_MINUTE, HOURS * PX_PER_HOUR - minutesFromGridStart * PX_PER_MINUTE),
      }
    })
    .filter((x): x is PositionedAppt => x !== null)
}

function onColumnClick(d: Date, ev: MouseEvent): void {
  const target = ev.currentTarget as HTMLElement
  const rect = target.getBoundingClientRect()
  const offsetY = ev.clientY - rect.top
  const minutes = Math.max(0, Math.floor(offsetY / PX_PER_MINUTE / 30) * 30)
  const slotStart = new Date(d)
  slotStart.setHours(START_HOUR, 0, 0, 0)
  slotStart.setMinutes(minutes)
  emit('select-slot', slotStart)
}

function isToday(d: Date): boolean {
  return sameDay(d, new Date())
}
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
    <!-- Day headers -->
    <div class="grid border-b border-line" :style="{ gridTemplateColumns: '64px repeat(7, minmax(0, 1fr))' }">
      <div class="border-r border-line bg-surface-2" />
      <div
        v-for="d in days"
        :key="d.toISOString()"
        class="flex flex-col items-center border-r border-line bg-surface-2 px-2 py-2 text-xs last:border-r-0"
      >
        <span class="text-ink-muted uppercase tracking-wide text-[10px]">{{ d.toLocaleDateString(undefined, { weekday: 'short' }) }}</span>
        <span
          class="mt-0.5 inline-flex h-7 w-7 items-center justify-center rounded-full text-sm font-semibold"
          :class="isToday(d) ? 'bg-primary-600 text-white' : 'text-ink'"
        >
          {{ d.getDate() }}
        </span>
      </div>
    </div>

    <!-- Scrollable grid -->
    <div class="relative flex-1 overflow-auto">
      <div
        class="relative grid"
        :style="{
          gridTemplateColumns: '64px repeat(7, minmax(0, 1fr))',
          gridTemplateRows: `repeat(${HOURS}, ${PX_PER_HOUR}px)`,
        }"
      >
        <!-- Hour gutter -->
        <template v-for="(label, h) in hourLabels" :key="`hr-${h}`">
          <div
            class="border-r border-t border-line bg-surface-2 pr-2 text-right text-[10px] font-medium text-ink-muted"
            :style="{ gridColumn: 1, gridRow: h + 1 }"
          >
            <span class="block -translate-y-1.5">{{ label }}</span>
          </div>
        </template>

        <!-- Day columns -->
        <template v-for="(d, colIdx) in days" :key="`col-${d.toISOString()}`">
          <div
            class="relative border-r border-line last:border-r-0"
            :class="isToday(d) ? 'bg-primary-50/40 dark:bg-primary-700/5' : ''"
            :style="{
              gridColumn: colIdx + 2,
              gridRow: `1 / span ${HOURS}`,
            }"
            @click="onColumnClick(d, $event)"
          >
            <!-- Hour gridlines -->
            <div
              v-for="h in HOURS"
              :key="`gl-${h}`"
              class="absolute inset-x-0 border-t border-line"
              :style="{ top: `${h * PX_PER_HOUR}px` }"
            />
            <!-- Half-hour gridlines -->
            <div
              v-for="h in HOURS"
              :key="`gl-half-${h}`"
              class="absolute inset-x-0 border-t border-dashed border-line/60"
              :style="{ top: `${(h - 0.5) * PX_PER_HOUR}px` }"
            />
            <!-- Appointments -->
            <div
              v-for="pa in appointmentsForDay(d)"
              :key="pa.appt.id"
              class="absolute inset-x-1"
              :style="{ top: `${pa.topPx}px`, height: `${pa.heightPx}px` }"
              @click.stop
            >
              <AppointmentBlock
                :appointment="pa.appt"
                compact
                @click="emit('select-appointment', pa.appt)"
              />
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
