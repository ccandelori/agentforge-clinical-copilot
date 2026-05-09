<script setup lang="ts">
import { computed } from 'vue'

import type { Medication } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import { relativeTime } from '@/composables/useRelativeTime'

/**
 * Past Prescriptions card.
 *
 * The dashboard already shows current/active medications in
 * `MedicationsCard.vue`. This card surfaces the closed end of the same
 * `MedicationRequest` stream — completed and stopped scripts — so a
 * clinician can scan medication history without toggling the
 * "show inactive" affordance.
 *
 * The card consumes the same `Medication[]` projection as
 * `MedicationsCard.vue` (the upstream `getMedications()` helper merges
 * `MedicationRequest` and `MedicationStatement` for us); we simply
 * filter by status downstream so a single FHIR round-trip serves both
 * cards.
 */

interface Props {
  readonly medications: readonly Medication[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

// Stopped first (more clinically interesting — discontinued vs ran-its-course),
// then completed; tie-break by prescribed date desc so the most recent
// activity bubbles up.
const past = computed<readonly Medication[]>(() => {
  const filtered = props.medications.filter(
    (m) => m.status === 'completed' || m.status === 'stopped',
  )
  return [...filtered].sort((a, b) => {
    if (a.status !== b.status) return a.status === 'stopped' ? -1 : 1
    return b.prescribedDate.localeCompare(a.prescribedDate)
  })
})

function statusVariant(status: Medication['status']): 'neutral' | 'warning' {
  return status === 'stopped' ? 'warning' : 'neutral'
}
</script>

<template>
  <BaseCard :padded="false">
    <template #title>
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold tracking-tight">Past Prescriptions</h2>
        <BaseBadge variant="neutral">{{ past.length }}</BaseBadge>
      </div>
    </template>

    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 3"
        :key="n"
        class="flex animate-pulse flex-col gap-2 rounded-md border border-line p-3"
      >
        <div class="h-4 w-1/2 rounded bg-surface-2" />
        <div class="h-3 w-1/3 rounded bg-surface-2" />
      </div>
    </div>

    <BaseEmptyState
      v-else-if="past.length === 0"
      icon="℞"
      title="No past prescriptions"
      message="No completed or discontinued medications on file."
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="med in past"
        :key="med.id"
        class="grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-1 px-5 py-3"
      >
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="truncate text-sm font-medium">{{ med.name }}</span>
            <span v-if="med.dose" class="text-xs text-ink-muted">·</span>
            <span v-if="med.dose" class="text-xs tabular-nums text-ink-muted">
              {{ med.dose }}
            </span>
          </div>
          <p class="truncate text-xs text-ink-muted">
            <template v-if="med.frequency">
              {{ med.frequency }} <span aria-hidden="true">·</span>
            </template>
            <template v-if="med.route">
              {{ med.route }} <span aria-hidden="true">·</span>
            </template>
            {{ med.prescriber || 'Unknown prescriber' }}
          </p>
        </div>
        <div class="flex flex-col items-end gap-1">
          <BaseBadge :variant="statusVariant(med.status)">{{ med.status }}</BaseBadge>
          <span v-if="med.prescribedDate" class="text-[11px] text-ink-muted">
            Prescribed {{ relativeTime(med.prescribedDate) }}
          </span>
        </div>
      </li>
    </ul>
  </BaseCard>
</template>
