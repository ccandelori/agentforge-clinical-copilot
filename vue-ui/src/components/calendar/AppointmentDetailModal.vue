<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import type { Appointment, AppointmentStatus } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { formatDateLong, formatTime } from '@/lib/dates'
import { paletteFor } from '@/stores/calendar'

interface Props {
  open: boolean
  appointment: Appointment | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
}>()

const router = useRouter()

const palette = computed(() =>
  props.appointment ? paletteFor(props.appointment.providerName) : null,
)

function badgeVariant(status: AppointmentStatus): 'neutral' | 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'arrived':
      return 'success'
    case 'fulfilled':
      return 'neutral'
    case 'cancelled':
      return 'danger'
    case 'no-show':
      return 'warning'
    case 'booked':
      return 'info'
  }
}

const startDate = computed<Date | null>(() =>
  props.appointment ? new Date(props.appointment.start) : null,
)
const endDate = computed<Date | null>(() =>
  props.appointment ? new Date(props.appointment.end) : null,
)

function close(): void {
  emit('update:open', false)
}

function openChart(): void {
  if (!props.appointment) return
  void router.push({ name: 'patient-dashboard', params: { id: props.appointment.patientId } })
  close()
}
</script>

<template>
  <BaseModal :open="open" title="Appointment details" @update:open="emit('update:open', $event)">
    <div v-if="appointment" class="flex flex-col gap-4">
      <header class="flex items-start justify-between gap-3">
        <div>
          <h3 class="text-base font-semibold tracking-tight text-ink">
            {{ appointment.patientName }}
          </h3>
          <p class="text-xs text-ink-muted">
            {{ appointment.reason }}
          </p>
        </div>
        <BaseBadge :variant="badgeVariant(appointment.status)">
          {{ appointment.status }}
        </BaseBadge>
      </header>

      <div
        v-if="palette"
        class="rounded-lg border-l-4 px-3 py-2"
        :class="[palette.bg, palette.border, palette.text]"
      >
        <div class="text-xs font-semibold uppercase tracking-wide opacity-70">Provider</div>
        <div class="text-sm font-medium">{{ appointment.providerName }}</div>
      </div>

      <dl class="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-muted">Date</dt>
          <dd class="font-medium text-ink">{{ startDate ? formatDateLong(startDate) : '' }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-muted">Time</dt>
          <dd class="font-medium text-ink">
            {{ startDate && endDate ? `${formatTime(startDate)} – ${formatTime(endDate)}` : '' }}
          </dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-muted">Patient ID</dt>
          <dd class="font-mono text-xs text-ink">{{ appointment.patientId }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-muted">Appointment ID</dt>
          <dd class="font-mono text-xs text-ink">{{ appointment.id }}</dd>
        </div>
      </dl>
    </div>
    <p v-else class="text-sm text-ink-muted">No appointment selected.</p>

    <template #footer>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <BaseButton variant="danger" size="sm" :disabled="!appointment" @click="close">
            Cancel appointment
          </BaseButton>
          <BaseButton variant="secondary" size="sm" :disabled="!appointment" @click="close">
            Reschedule
          </BaseButton>
        </div>
        <BaseButton size="sm" :disabled="!appointment" @click="openChart">
          Open chart
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>
