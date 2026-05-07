<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { Encounter, EncounterStatus } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'

interface Props {
  readonly encounters: readonly Encounter[]
  readonly loading?: boolean
  readonly limit?: number
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  limit: 3,
})

const recent = computed<readonly Encounter[]>(() =>
  [...props.encounters]
    .sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
    .slice(0, props.limit),
)

function dateFormatted(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function statusVariant(
  status: EncounterStatus,
): 'success' | 'info' | 'warning' | 'neutral' {
  switch (status) {
    case 'finished':
      return 'success'
    case 'in-progress':
      return 'info'
    case 'scheduled':
      return 'warning'
    case 'cancelled':
      return 'neutral'
  }
}
</script>

<template>
  <BaseCard title="Recent encounters" :padded="false">
    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 3"
        :key="n"
        class="flex animate-pulse items-center gap-3 rounded-md border border-line p-3"
      >
        <div class="h-10 w-10 rounded bg-surface-2" />
        <div class="flex-1 space-y-1">
          <div class="h-3 w-1/2 rounded bg-surface-2" />
          <div class="h-3 w-1/3 rounded bg-surface-2" />
        </div>
      </div>
    </div>

    <BaseEmptyState
      v-else-if="recent.length === 0"
      icon="📋"
      title="No encounters yet"
      message="New visits will appear in this list."
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="enc in recent"
        :key="enc.id"
        class="flex items-center gap-3 px-5 py-3"
      >
        <div
          class="flex h-10 w-10 shrink-0 flex-col items-center justify-center rounded-md border border-line bg-surface-2 text-center text-ink"
          aria-hidden="true"
        >
          <span class="text-[9px] font-medium uppercase tracking-wide text-ink-muted">
            {{ new Date(enc.date).toLocaleDateString(undefined, { month: 'short' }) }}
          </span>
          <span class="text-sm font-semibold leading-none">
            {{ new Date(enc.date).getDate() }}
          </span>
        </div>
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="truncate text-sm font-medium">{{ enc.type }}</span>
            <BaseBadge :variant="statusVariant(enc.status)">
              {{ enc.status }}
            </BaseBadge>
          </div>
          <p class="truncate text-xs text-ink-muted">
            {{ dateFormatted(enc.date) }}
            <span aria-hidden="true">·</span>
            {{ enc.providerName }}
            <span aria-hidden="true">·</span>
            <span class="italic">{{ enc.reason }}</span>
          </p>
        </div>
        <RouterLink
          :to="{ name: 'encounter', params: { id: enc.id } }"
          class="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-ink hover:bg-surface-2"
        >
          Open
        </RouterLink>
      </li>
    </ul>

    <template v-if="encounters.length > limit" #footer>
      <span class="text-xs text-ink-muted">
        Showing {{ recent.length }} of {{ encounters.length }} encounters
        <span aria-hidden="true">·</span>
        <span class="font-medium text-primary-600 hover:underline cursor-pointer">
          View all
        </span>
      </span>
    </template>
  </BaseCard>
</template>
