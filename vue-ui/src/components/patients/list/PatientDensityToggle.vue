<script setup lang="ts">
export type Density = 'comfortable' | 'compact'

interface Props {
  modelValue: Density
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Density): void
}>()

const options: ReadonlyArray<{ readonly value: Density; readonly label: string }> = [
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'compact', label: 'Compact' },
]

function select(value: Density): void {
  emit('update:modelValue', value)
}
</script>

<template>
  <div
    role="radiogroup"
    aria-label="Row density"
    class="inline-flex items-center rounded-lg border border-line bg-surface p-0.5"
  >
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="radio"
      :aria-checked="modelValue === opt.value"
      class="rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      :class="
        modelValue === opt.value
          ? 'bg-primary-600 text-white'
          : 'text-ink-muted hover:text-ink'
      "
      @click="select(opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>
