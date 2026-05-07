<script setup lang="ts">
import { computed } from 'vue'

import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import ProblemTypeahead from './ProblemTypeahead.vue'
import type { AssessmentItem } from '@/composables/useEncounterDraft'

interface Props {
  problems: readonly AssessmentItem[]
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), { disabled: false })

const emit = defineEmits<{
  (e: 'add', item: AssessmentItem): void
  (e: 'remove', id: string): void
}>()

const excludeCodes = computed<readonly string[]>(() =>
  props.problems.map((p) => p.icd10),
)

function onPick(entry: { code: string; description: string }): void {
  const id = `prob-${entry.code}-${Date.now().toString(36)}`
  emit('add', { id, icd10: entry.code, description: entry.description })
}
</script>

<template>
  <BaseCard title="Assessment">
    <div class="flex flex-col gap-3">
      <ProblemTypeahead
        :disabled="disabled"
        :exclude-codes="excludeCodes"
        @pick="onPick"
      />

      <div v-if="problems.length === 0">
        <BaseEmptyState
          title="No problems yet"
          message="Search above to add diagnoses to today's assessment."
        />
      </div>

      <ul v-else class="flex flex-wrap gap-2">
        <li
          v-for="p in problems"
          :key="p.id"
          class="group inline-flex items-center gap-2 rounded-full border border-line bg-surface-2 py-1 pl-3 pr-1 text-sm"
        >
          <span class="font-mono text-xs text-ink-muted">{{ p.icd10 }}</span>
          <span class="text-ink">{{ p.description }}</span>
          <button
            type="button"
            class="ml-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-ink-muted hover:bg-danger-100 hover:text-danger-700 disabled:opacity-40"
            :disabled="disabled"
            :aria-label="`Remove ${p.description}`"
            @click="emit('remove', p.id)"
          >
            ×
          </button>
        </li>
      </ul>
    </div>
  </BaseCard>
</template>
