<script setup lang="ts">
import { computed } from 'vue'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

interface Props {
  variant?: Variant
  size?: Size
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  block?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'primary',
  size: 'md',
  type: 'button',
  disabled: false,
  loading: false,
  block: false,
})

const variantClass = computed<string>(() => {
  switch (props.variant) {
    case 'primary':
      return 'bg-primary-600 text-white hover:bg-primary-700 focus-visible:ring-primary-500'
    case 'secondary':
      return 'bg-surface text-ink border border-line hover:bg-surface-2 focus-visible:ring-primary-500'
    case 'ghost':
      return 'bg-transparent text-ink hover:bg-surface-2 focus-visible:ring-primary-500'
    case 'danger':
      return 'bg-danger-600 text-white hover:bg-danger-700 focus-visible:ring-danger-500'
  }
})

const sizeClass = computed<string>(() => {
  switch (props.size) {
    case 'sm':
      return 'px-2.5 py-1 text-xs'
    case 'md':
      return 'px-3.5 py-2 text-sm'
    case 'lg':
      return 'px-4 py-2.5 text-base'
  }
})
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    class="inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
    :class="[variantClass, sizeClass, block ? 'w-full' : '']"
  >
    <span
      v-if="loading"
      class="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
      aria-hidden="true"
    />
    <slot />
  </button>
</template>
