<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Medication } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import { relativeTime } from '@/composables/useRelativeTime'

interface Props {
  readonly medications: readonly Medication[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

const showInactive = ref<boolean>(false)

const visible = computed<readonly Medication[]>(() => {
  if (showInactive.value) return props.medications
  return props.medications.filter((m) => m.status === 'active')
})

const activeCount = computed<number>(
  () => props.medications.filter((m) => m.status === 'active').length,
)
const inactiveCount = computed<number>(
  () => props.medications.length - activeCount.value,
)

function statusVariant(
  status: Medication['status'],
): 'success' | 'neutral' | 'warning' {
  switch (status) {
    case 'active':
      return 'success'
    case 'completed':
      return 'neutral'
    case 'stopped':
      return 'warning'
  }
}
</script>

<template>
  <BaseCard :padded="false">
    <template #title>
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold tracking-tight">Medications</h2>
        <BaseBadge variant="neutral">{{ activeCount }} active</BaseBadge>
      </div>
    </template>
    <template #actions>
      <label class="flex items-center gap-1.5 text-xs text-ink-muted">
        <input
          v-model="showInactive"
          type="checkbox"
          class="h-3.5 w-3.5 rounded border-line text-primary-600 focus:ring-primary-500/40"
        />
        Show inactive ({{ inactiveCount }})
      </label>
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
      v-else-if="visible.length === 0"
      icon="℞"
      title="No medications"
      :message="
        activeCount === 0
          ? 'No active medications on file.'
          : 'Toggle Show inactive to see prior prescriptions.'
      "
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="med in visible"
        :key="med.id"
        class="grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-1 px-5 py-3"
      >
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="truncate text-sm font-medium">{{ med.name }}</span>
            <span class="text-xs text-ink-muted">·</span>
            <span class="text-xs tabular-nums text-ink-muted">{{ med.dose }}</span>
          </div>
          <p class="truncate text-xs text-ink-muted">
            {{ med.frequency }} <span aria-hidden="true">·</span> {{ med.route }}
            <span aria-hidden="true">·</span> {{ med.prescriber }}
          </p>
        </div>
        <div class="flex flex-col items-end gap-1">
          <BaseBadge :variant="statusVariant(med.status)">{{ med.status }}</BaseBadge>
          <span class="text-[11px] text-ink-muted">
            Started {{ relativeTime(med.prescribedDate) }}
          </span>
          <button
            v-if="med.status === 'active'"
            type="button"
            class="text-[11px] font-medium text-primary-600 hover:underline"
          >
            Refill
          </button>
        </div>
      </li>
    </ul>
  </BaseCard>
</template>
