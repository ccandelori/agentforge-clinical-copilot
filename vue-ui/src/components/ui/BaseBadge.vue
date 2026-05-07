<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

interface Props {
  variant?: Variant
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'neutral',
})

const cls = computed<string>(() => {
  switch (props.variant) {
    case 'success':
      return 'bg-success-100 text-success-700 dark:bg-success-700/20 dark:text-success-500'
    case 'warning':
      return 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-500'
    case 'danger':
      return 'bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-500'
    case 'info':
      return 'bg-info-100 text-info-700 dark:bg-info-700/20 dark:text-info-500'
    case 'neutral':
      return 'bg-neutral-100 text-neutral-700 dark:bg-neutral-700/40 dark:text-neutral-300'
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
    :class="cls"
  >
    <slot />
  </span>
</template>
