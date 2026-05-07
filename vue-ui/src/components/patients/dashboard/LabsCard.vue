<script setup lang="ts">
import { computed } from 'vue'

import type { LabFlag, LabResult } from '@/api/mock'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import { relativeTime } from '@/composables/useRelativeTime'

interface Props {
  readonly labs: readonly LabResult[]
  readonly loading?: boolean
  readonly limit?: number
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  limit: 6,
})

const recent = computed<readonly LabResult[]>(() =>
  [...props.labs]
    .sort(
      (a, b) =>
        new Date(b.resultedAt).getTime() - new Date(a.resultedAt).getTime(),
    )
    .slice(0, props.limit),
)

function flagClass(flag: LabFlag | undefined): string {
  switch (flag) {
    case 'critical':
      return 'text-danger-700 bg-danger-100 dark:bg-danger-700/20'
    case 'high':
      return 'text-warning-700 bg-warning-100 dark:bg-warning-700/20'
    case 'low':
      return 'text-info-700 bg-info-100 dark:bg-info-700/20'
    case 'normal':
    case undefined:
      return 'text-ink-muted bg-surface-2'
  }
}

function flagSymbol(flag: LabFlag | undefined): string {
  switch (flag) {
    case 'critical':
      return '!!'
    case 'high':
      return 'H'
    case 'low':
      return 'L'
    case 'normal':
    case undefined:
      return ''
  }
}
</script>

<template>
  <BaseCard title="Lab results" :padded="false">
    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 4"
        :key="n"
        class="flex animate-pulse items-center gap-3 rounded-md border border-line p-3"
      >
        <div class="h-4 w-1/3 rounded bg-surface-2" />
        <div class="h-4 flex-1 rounded bg-surface-2" />
      </div>
    </div>

    <BaseEmptyState
      v-else-if="recent.length === 0"
      icon="🧪"
      title="No lab results"
      message="Recent lab results will appear here."
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="lab in recent"
        :key="lab.id"
        class="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 px-5 py-2.5"
      >
        <div class="min-w-0">
          <p class="truncate text-sm font-medium">{{ lab.name }}</p>
          <p class="truncate text-[11px] text-ink-muted">
            {{ relativeTime(lab.resultedAt) }}
            <span v-if="lab.referenceRange" aria-hidden="true">·</span>
            <span v-if="lab.referenceRange">ref {{ lab.referenceRange }}</span>
          </p>
        </div>
        <div class="text-right">
          <span class="text-sm font-semibold tabular-nums">{{ lab.value }}</span>
          <span v-if="lab.unit" class="ml-1 text-[11px] text-ink-muted">
            {{ lab.unit }}
          </span>
        </div>
        <span
          class="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded px-1 text-[10px] font-bold tracking-wide"
          :class="flagClass(lab.flag)"
          :aria-label="lab.flag ?? 'normal'"
        >
          {{ flagSymbol(lab.flag) || '·' }}
        </span>
      </li>
    </ul>
  </BaseCard>
</template>
