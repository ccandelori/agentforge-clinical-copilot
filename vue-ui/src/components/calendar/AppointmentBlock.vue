<script setup lang="ts">
import { computed } from 'vue'

import type { Appointment, AppointmentStatus } from '@/api/mock'
import { formatTime } from '@/lib/dates'
import { paletteFor } from '@/stores/calendar'

interface Props {
  appointment: Appointment
  /** Compact rendering for crowded month/week cells. */
  compact?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
})

defineEmits<{
  (e: 'click', appointment: Appointment): void
}>()

const palette = computed(() => paletteFor(props.appointment.providerName))

const start = computed<Date>(() => new Date(props.appointment.start))
const end = computed<Date>(() => new Date(props.appointment.end))

const timeRange = computed<string>(
  () => `${formatTime(start.value)} – ${formatTime(end.value)}`,
)

interface StatusStyle {
  readonly dotClass: string
  readonly label: string
}

function statusStyle(status: AppointmentStatus): StatusStyle {
  switch (status) {
    case 'arrived':
      return { dotClass: 'bg-success-500', label: 'Arrived' }
    case 'fulfilled':
      return { dotClass: 'bg-neutral-400', label: 'Fulfilled' }
    case 'cancelled':
      return { dotClass: 'bg-danger-500', label: 'Cancelled' }
    case 'no-show':
      return { dotClass: 'bg-warning-500', label: 'No-show' }
    case 'booked':
      return { dotClass: 'bg-info-500', label: 'Booked' }
  }
}

const status = computed<StatusStyle>(() => statusStyle(props.appointment.status))
</script>

<template>
  <button
    type="button"
    class="group flex w-full flex-col items-start gap-0.5 overflow-hidden rounded-md border-l-4 px-2 py-1 text-left text-xs shadow-sm transition hover:shadow-card-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    :class="[palette.bg, palette.border, palette.text]"
    @click="$emit('click', appointment)"
  >
    <div class="flex w-full items-center gap-1.5">
      <span
        class="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
        :class="status.dotClass"
        :title="status.label"
        aria-hidden="true"
      />
      <span class="min-w-0 flex-1 truncate font-semibold">
        {{ appointment.patientName }}
      </span>
    </div>
    <div v-if="!compact" class="w-full truncate text-[11px] opacity-90">
      {{ timeRange }}
    </div>
    <div v-if="!compact" class="flex w-full items-center justify-between gap-1 text-[10px] uppercase tracking-wide opacity-80">
      <span class="truncate">{{ appointment.providerName }}</span>
      <span class="shrink-0 rounded-full bg-white/60 px-1.5 py-px text-[9px] font-medium dark:bg-black/20">
        {{ appointment.reason }}
      </span>
    </div>
  </button>
</template>
