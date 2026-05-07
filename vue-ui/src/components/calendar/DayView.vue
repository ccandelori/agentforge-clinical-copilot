<script setup lang="ts">
import { computed } from 'vue'

import type { Appointment } from '@/api/mock'
import AppointmentBlock from '@/components/calendar/AppointmentBlock.vue'
import { formatTime, sameDay, startOfDay } from '@/lib/dates'
import { PROVIDERS, useCalendarStore } from '@/stores/calendar'

interface Props {
  date: Date
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select-slot', slotStart: Date, providerName: string): void
  (e: 'select-appointment', appointment: Appointment): void
}>()

const store = useCalendarStore()

/** 30-minute slot rows from 7:00 to 18:00 inclusive (i.e. last slot starts 17:30). */
const SLOTS_PER_DAY = 22 // (18 - 7) * 2
const SLOT_MINUTES = 30
const START_HOUR = 7

interface Slot {
  readonly start: Date
  readonly minutesFromStart: number
  readonly hourLabel: string | null
  readonly halfHour: boolean
}

const slots = computed<readonly Slot[]>(() => {
  const out: Slot[] = []
  const base = startOfDay(props.date)
  base.setHours(START_HOUR, 0, 0, 0)
  for (let i = 0; i < SLOTS_PER_DAY; i += 1) {
    const start = new Date(base.getTime() + i * SLOT_MINUTES * 60_000)
    const halfHour = start.getMinutes() === 30
    out.push({
      start,
      minutesFromStart: i * SLOT_MINUTES,
      hourLabel: halfHour ? null : formatTime(start),
      halfHour,
    })
  }
  return out
})

/**
 * The four columns are the canonical PROVIDERS list. We pin them so the
 * grid is stable as the provider filter changes — filtered-out columns
 * just render empty (and we visually dim their header).
 */
const columns = computed<readonly string[]>(() => PROVIDERS.slice(0, 4))

const dayAppointments = computed<readonly Appointment[]>(() =>
  store.appointmentsForDay(props.date),
)

interface PositionedAppointment {
  readonly appt: Appointment
  readonly column: string
  readonly topPx: number
  readonly heightPx: number
}

const PX_PER_MINUTE = 1.0 // 30 px per slot row matches `h-[30px]` below.
const ROW_HEIGHT_PX = 30

function appointmentsForColumn(provider: string): readonly PositionedAppointment[] {
  const out: PositionedAppointment[] = []
  for (const appt of dayAppointments.value) {
    if (appt.providerName !== provider) continue
    const start = new Date(appt.start)
    const end = new Date(appt.end)
    if (!sameDay(start, props.date)) continue
    const minutesFromGridStart = (start.getHours() - START_HOUR) * 60 + start.getMinutes()
    const durationMinutes = Math.max(15, (end.getTime() - start.getTime()) / 60_000)
    if (minutesFromGridStart < 0) continue
    if (minutesFromGridStart >= SLOTS_PER_DAY * SLOT_MINUTES) continue
    out.push({
      appt,
      column: provider,
      topPx: minutesFromGridStart * PX_PER_MINUTE,
      heightPx: Math.min(durationMinutes * PX_PER_MINUTE, SLOTS_PER_DAY * SLOT_MINUTES * PX_PER_MINUTE - minutesFromGridStart * PX_PER_MINUTE),
    })
  }
  return out
}

function onSlotClick(slot: Slot, provider: string): void {
  emit('select-slot', slot.start, provider)
}

const isToday = computed<boolean>(() => sameDay(props.date, new Date()))
const todayLineMinutes = computed<number | null>(() => {
  if (!isToday.value) return null
  const now = new Date()
  const minutes = (now.getHours() - START_HOUR) * 60 + now.getMinutes()
  if (minutes < 0 || minutes > SLOTS_PER_DAY * SLOT_MINUTES) return null
  return minutes
})
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
    <!-- Provider column headers -->
    <div class="grid border-b border-line" :style="{ gridTemplateColumns: `64px repeat(${columns.length}, minmax(0, 1fr))` }">
      <div class="border-r border-line bg-surface-2" />
      <div
        v-for="col in columns"
        :key="col"
        class="flex items-center justify-center gap-2 border-r border-line bg-surface-2 px-3 py-2 text-xs font-semibold text-ink last:border-r-0"
        :class="store.providerFilter.size > 0 && !store.providerFilter.has(col) ? 'opacity-40' : ''"
      >
        <span
          class="inline-block h-2 w-2 rounded-full"
          :class="['bg-primary-500', 'bg-info-500', 'bg-warning-500', 'bg-success-500'][columns.indexOf(col) % 4]"
        />
        {{ col }}
      </div>
    </div>

    <!-- Scrollable grid -->
    <div class="relative flex-1 overflow-auto">
      <div
        class="relative grid"
        :style="{
          gridTemplateColumns: `64px repeat(${columns.length}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${SLOTS_PER_DAY}, ${ROW_HEIGHT_PX}px)`,
        }"
      >
        <!-- Time gutter -->
        <template v-for="(slot, idx) in slots" :key="`time-${idx}`">
          <div
            class="border-r border-line bg-surface-2 pr-2 text-right text-[10px] font-medium text-ink-muted"
            :class="slot.halfHour ? 'border-t border-dashed border-line/60' : 'border-t border-line'"
            :style="{ gridColumn: 1, gridRow: idx + 1 }"
          >
            <span v-if="!slot.halfHour" class="block -translate-y-1.5">{{ slot.hourLabel }}</span>
          </div>
        </template>

        <!-- Provider columns: each cell is a clickable slot -->
        <template v-for="(col, colIdx) in columns" :key="`col-${col}`">
          <button
            v-for="(slot, idx) in slots"
            :key="`${col}-${idx}`"
            type="button"
            class="border-r border-line text-left transition hover:bg-primary-50 dark:hover:bg-primary-700/10 last:border-r-0"
            :class="slot.halfHour ? 'border-t border-dashed border-line/60' : 'border-t border-line'"
            :style="{ gridColumn: colIdx + 2, gridRow: idx + 1 }"
            @click="onSlotClick(slot, col)"
          />
        </template>

        <!-- Today's "now" line -->
        <div
          v-if="todayLineMinutes !== null"
          class="pointer-events-none absolute left-0 right-0 z-10 flex items-center"
          :style="{ top: `${todayLineMinutes * PX_PER_MINUTE}px` }"
        >
          <div class="h-0.5 w-full bg-danger-500/70" />
        </div>

        <!-- Absolutely-positioned appointment blocks (one container per column) -->
        <template v-for="(col, colIdx) in columns" :key="`appts-${col}`">
          <div
            class="pointer-events-none relative"
            :style="{
              gridColumn: colIdx + 2,
              gridRow: `1 / span ${SLOTS_PER_DAY}`,
            }"
          >
            <div
              v-for="pa in appointmentsForColumn(col)"
              :key="pa.appt.id"
              class="pointer-events-auto absolute inset-x-1"
              :style="{ top: `${pa.topPx}px`, height: `${pa.heightPx}px` }"
            >
              <AppointmentBlock
                :appointment="pa.appt"
                @click="emit('select-appointment', pa.appt)"
              />
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
