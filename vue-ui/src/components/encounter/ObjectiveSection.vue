<script setup lang="ts">
import { ref } from 'vue'

import BaseCard from '@/components/ui/BaseCard.vue'
import AutoGrowTextarea from './AutoGrowTextarea.vue'
import VitalsInputGrid from './VitalsInputGrid.vue'
import type { EncounterVitalsInput } from '@/composables/useEncounterDraft'

interface Props {
  vitals: EncounterVitalsInput
  examHeart: string
  examLungs: string
  examAbdomen: string
  examNeuro: string
  disabled?: boolean
}

withDefaults(defineProps<Props>(), { disabled: false })

defineEmits<{
  (e: 'update:vitals', value: EncounterVitalsInput): void
  (e: 'update:examHeart', value: string): void
  (e: 'update:examLungs', value: string): void
  (e: 'update:examAbdomen', value: string): void
  (e: 'update:examNeuro', value: string): void
}>()

interface ExamSystemKey {
  readonly key: 'heart' | 'lungs' | 'abdomen' | 'neuro'
  readonly label: string
  readonly placeholder: string
}

const systems: readonly ExamSystemKey[] = [
  { key: 'heart', label: 'Cardiovascular', placeholder: 'Rate, rhythm, murmurs, pulses…' },
  { key: 'lungs', label: 'Respiratory', placeholder: 'Effort, breath sounds, wheezing…' },
  { key: 'abdomen', label: 'Abdomen', placeholder: 'Soft, tender, bowel sounds, organomegaly…' },
  { key: 'neuro', label: 'Neurologic', placeholder: 'Mental status, cranial nerves, motor, sensory…' },
]

const open = ref<Record<'heart' | 'lungs' | 'abdomen' | 'neuro', boolean>>({
  heart: true,
  lungs: true,
  abdomen: false,
  neuro: false,
})

function toggle(key: ExamSystemKey['key']): void {
  open.value[key] = !open.value[key]
}
</script>

<template>
  <BaseCard title="Objective">
    <div class="flex flex-col gap-5">
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Vital signs
        </h3>
        <VitalsInputGrid
          :vitals="vitals"
          :disabled="disabled"
          @update="$emit('update:vitals', $event)"
        />
      </div>

      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Physical exam
        </h3>
        <div class="flex flex-col gap-2">
          <div
            v-for="s in systems"
            :key="s.key"
            class="rounded-lg border border-line bg-surface"
          >
            <button
              type="button"
              class="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-ink hover:bg-surface-2"
              @click="toggle(s.key)"
            >
              <span>{{ s.label }}</span>
              <span class="text-ink-muted" aria-hidden="true">
                {{ open[s.key] ? '−' : '+' }}
              </span>
            </button>
            <div v-if="open[s.key]" class="border-t border-line p-3">
              <AutoGrowTextarea
                v-if="s.key === 'heart'"
                :model-value="examHeart"
                :placeholder="s.placeholder"
                :rows="2"
                :disabled="disabled"
                @update:model-value="$emit('update:examHeart', $event)"
              />
              <AutoGrowTextarea
                v-else-if="s.key === 'lungs'"
                :model-value="examLungs"
                :placeholder="s.placeholder"
                :rows="2"
                :disabled="disabled"
                @update:model-value="$emit('update:examLungs', $event)"
              />
              <AutoGrowTextarea
                v-else-if="s.key === 'abdomen'"
                :model-value="examAbdomen"
                :placeholder="s.placeholder"
                :rows="2"
                :disabled="disabled"
                @update:model-value="$emit('update:examAbdomen', $event)"
              />
              <AutoGrowTextarea
                v-else
                :model-value="examNeuro"
                :placeholder="s.placeholder"
                :rows="2"
                :disabled="disabled"
                @update:model-value="$emit('update:examNeuro', $event)"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  </BaseCard>
</template>
