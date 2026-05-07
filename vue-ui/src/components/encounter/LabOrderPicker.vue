<script setup lang="ts">
import { computed, ref } from 'vue'

interface Props {
  selected: readonly string[]
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), { disabled: false })

const emit = defineEmits<{
  (e: 'toggle', label: string): void
}>()

const LAB_OPTIONS: readonly string[] = [
  'CBC with differential',
  'Comprehensive metabolic panel',
  'Basic metabolic panel',
  'Lipid panel',
  'HbA1c',
  'TSH',
  'Free T4',
  'Vitamin D, 25-OH',
  'Vitamin B12',
  'Ferritin',
  'CRP',
  'ESR',
  'Urinalysis',
  'Urine culture',
  'PT/INR',
  'PSA',
  'Hepatic panel',
  'BNP',
  'Troponin I',
  'D-dimer',
]

const query = ref<string>('')

const isSelected = (label: string): boolean => props.selected.includes(label)

const filtered = computed<readonly string[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return LAB_OPTIONS
  return LAB_OPTIONS.filter((l) => l.toLowerCase().includes(q))
})
</script>

<template>
  <div class="flex flex-col gap-2">
    <input
      v-model="query"
      type="text"
      placeholder="Filter labs…"
      :disabled="disabled"
      class="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
    />
    <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      <label
        v-for="lab in filtered"
        :key="lab"
        class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-2"
      >
        <input
          type="checkbox"
          :checked="isSelected(lab)"
          :disabled="disabled"
          class="h-4 w-4 rounded border-line text-primary-600 focus:ring-primary-500"
          @change="emit('toggle', lab)"
        />
        <span :class="isSelected(lab) ? 'text-ink font-medium' : 'text-ink'">
          {{ lab }}
        </span>
      </label>
    </div>
  </div>
</template>
