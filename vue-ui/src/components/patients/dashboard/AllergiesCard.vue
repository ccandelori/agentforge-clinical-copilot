<script setup lang="ts">
import { computed } from 'vue'

import type { Allergy, AllergySeverity } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import { relativeTime } from '@/composables/useRelativeTime'

interface Props {
  readonly allergies: readonly Allergy[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

interface ClassifiedAllergy extends Allergy {
  readonly category: 'drug' | 'food' | 'environmental'
}

const DRUG_KEYWORDS = ['penicillin', 'amoxicillin', 'sulfa', 'aspirin', 'codeine']
const FOOD_KEYWORDS = ['peanut', 'shellfish', 'milk', 'egg', 'soy', 'wheat', 'tree nut']

function classify(substance: string): ClassifiedAllergy['category'] {
  const s = substance.toLowerCase()
  if (DRUG_KEYWORDS.some((k) => s.includes(k))) return 'drug'
  if (FOOD_KEYWORDS.some((k) => s.includes(k))) return 'food'
  return 'environmental'
}

const items = computed<readonly ClassifiedAllergy[]>(() =>
  props.allergies.map((a) => ({ ...a, category: classify(a.substance) })),
)

const SEVERITY_RANK: Readonly<Record<AllergySeverity, number>> = {
  severe: 0,
  moderate: 1,
  mild: 2,
}

const sorted = computed<readonly ClassifiedAllergy[]>(() =>
  [...items.value].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity],
  ),
)

function severityVariant(
  severity: AllergySeverity,
): 'danger' | 'warning' | 'info' {
  switch (severity) {
    case 'severe':
      return 'danger'
    case 'moderate':
      return 'warning'
    case 'mild':
      return 'info'
  }
}

function categoryLabel(c: ClassifiedAllergy['category']): string {
  switch (c) {
    case 'drug':
      return 'Drug'
    case 'food':
      return 'Food'
    case 'environmental':
      return 'Environment'
  }
}
</script>

<template>
  <BaseCard :padded="false">
    <template #title>
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold tracking-tight">Allergies</h2>
        <BaseBadge v-if="items.length > 0" variant="danger">
          {{ items.length }}
        </BaseBadge>
      </div>
    </template>

    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 2"
        :key="n"
        class="flex animate-pulse items-center gap-3 rounded-md border border-line p-3"
      >
        <div class="h-4 w-1/3 rounded bg-surface-2" />
        <div class="h-4 flex-1 rounded bg-surface-2" />
      </div>
    </div>

    <BaseEmptyState
      v-else-if="sorted.length === 0"
      icon="✓"
      title="No known allergies"
      message="No drug, food, or environmental allergies on file."
    />

    <ul v-else class="divide-y divide-line">
      <li
        v-for="allergy in sorted"
        :key="allergy.id"
        class="flex items-center gap-3 px-5 py-3"
      >
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="truncate text-sm font-medium">{{ allergy.substance }}</span>
            <span
              class="rounded border border-line px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted"
            >
              {{ categoryLabel(allergy.category) }}
            </span>
          </div>
          <p class="text-xs text-ink-muted">
            {{ allergy.reaction }}
            <span aria-hidden="true">·</span>
            noted {{ relativeTime(allergy.notedDate) }}
          </p>
        </div>
        <BaseBadge :variant="severityVariant(allergy.severity)">
          {{ allergy.severity }}
        </BaseBadge>
      </li>
    </ul>
  </BaseCard>
</template>
