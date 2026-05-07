<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Problem, ProblemStatus } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import { relativeTime } from '@/composables/useRelativeTime'

interface Props {
  readonly problems: readonly Problem[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

type SortKey = 'onset' | 'status' | 'description'

const sortKey = ref<SortKey>('onset')
const expanded = ref<Set<string>>(new Set<string>())

function toggle(id: string): void {
  const next = new Set(expanded.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expanded.value = next
}

const STATUS_RANK: Readonly<Record<ProblemStatus, number>> = {
  active: 0,
  inactive: 1,
  resolved: 2,
}

const sorted = computed<readonly Problem[]>(() => {
  const out = [...props.problems]
  switch (sortKey.value) {
    case 'onset':
      out.sort((a, b) => b.onsetDate.localeCompare(a.onsetDate))
      break
    case 'status':
      out.sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status])
      break
    case 'description':
      out.sort((a, b) => a.description.localeCompare(b.description))
      break
  }
  return out
})

function dotClass(status: ProblemStatus): string {
  switch (status) {
    case 'active':
      return 'bg-danger-500'
    case 'inactive':
      return 'bg-warning-500'
    case 'resolved':
      return 'bg-success-500'
  }
}

function statusVariant(status: ProblemStatus): 'danger' | 'warning' | 'success' {
  switch (status) {
    case 'active':
      return 'danger'
    case 'inactive':
      return 'warning'
    case 'resolved':
      return 'success'
  }
}
</script>

<template>
  <BaseCard title="Problem list" :padded="false">
    <template #actions>
      <label class="flex items-center gap-1.5 text-xs text-ink-muted">
        <span>Sort</span>
        <select
          v-model="sortKey"
          class="rounded-md border border-line bg-surface px-2 py-1 text-xs focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/40"
        >
          <option value="onset">Onset</option>
          <option value="status">Status</option>
          <option value="description">A–Z</option>
        </select>
      </label>
    </template>

    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 4"
        :key="n"
        class="flex animate-pulse items-center gap-3 rounded-md border border-line p-3"
      >
        <div class="h-2 w-2 rounded-full bg-surface-2" />
        <div class="h-4 flex-1 rounded bg-surface-2" />
        <div class="h-4 w-16 rounded bg-surface-2" />
      </div>
    </div>

    <BaseEmptyState
      v-else-if="sorted.length === 0"
      icon="✓"
      title="No problems on file"
      message="Conditions added to the chart will appear here."
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="problem in sorted"
        :key="problem.id"
        class="px-5 py-3 transition-colors hover:bg-surface-2"
      >
        <button
          type="button"
          class="flex w-full items-center gap-3 text-left"
          :aria-expanded="expanded.has(problem.id)"
          @click="toggle(problem.id)"
        >
          <span
            class="h-2 w-2 shrink-0 rounded-full"
            :class="dotClass(problem.status)"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="truncate text-sm font-medium">
                {{ problem.description }}
              </span>
              <span class="font-mono text-[11px] text-ink-muted">
                {{ problem.icd10 }}
              </span>
            </div>
            <p class="text-xs text-ink-muted">
              Onset {{ relativeTime(problem.onsetDate) }}
            </p>
          </div>
          <BaseBadge :variant="statusVariant(problem.status)">
            {{ problem.status }}
          </BaseBadge>
          <span
            class="text-ink-muted transition-transform"
            :class="expanded.has(problem.id) ? 'rotate-90' : ''"
            aria-hidden="true"
          >
            ›
          </span>
        </button>

        <div
          v-if="expanded.has(problem.id)"
          class="mt-2 grid grid-cols-2 gap-3 rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-ink-muted"
        >
          <div>
            <dt class="font-medium text-ink">ICD-10</dt>
            <dd class="font-mono">{{ problem.icd10 }}</dd>
          </div>
          <div>
            <dt class="font-medium text-ink">Onset date</dt>
            <dd>{{ problem.onsetDate }}</dd>
          </div>
        </div>
      </li>
    </ul>
  </BaseCard>
</template>
