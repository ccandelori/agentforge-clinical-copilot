<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import AutoGrowTextarea from './AutoGrowTextarea.vue'

interface Props {
  chiefComplaint: string
  hpi: string
  disabled?: boolean
}

withDefaults(defineProps<Props>(), { disabled: false })

defineEmits<{
  (e: 'update:chiefComplaint', value: string): void
  (e: 'update:hpi', value: string): void
}>()
</script>

<template>
  <BaseCard title="Subjective">
    <div class="flex flex-col gap-4">
      <BaseInput
        label="Chief complaint"
        placeholder="e.g. Chest pain x2 days"
        :model-value="chiefComplaint"
        :disabled="disabled"
        @update:model-value="$emit('update:chiefComplaint', $event)"
      />
      <AutoGrowTextarea
        label="History of present illness"
        placeholder="Onset, location, duration, character, aggravating/relieving factors, timing, severity…"
        :rows="4"
        :model-value="hpi"
        :disabled="disabled"
        @update:model-value="$emit('update:hpi', $event)"
      />
    </div>
  </BaseCard>
</template>
