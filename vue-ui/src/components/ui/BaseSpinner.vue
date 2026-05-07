<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  size?: 'sm' | 'md' | 'lg'
  label?: string
}

const props = withDefaults(defineProps<Props>(), {
  size: 'md',
  label: 'Loading',
})

const sizeClass = computed<string>(() => {
  switch (props.size) {
    case 'sm':
      return 'h-4 w-4 border-2'
    case 'md':
      return 'h-6 w-6 border-2'
    case 'lg':
      return 'h-10 w-10 border-[3px]'
  }
})
</script>

<template>
  <span
    role="status"
    class="inline-block animate-spin rounded-full border-current border-t-transparent text-primary-600"
    :class="sizeClass"
    :aria-label="label"
  />
</template>
