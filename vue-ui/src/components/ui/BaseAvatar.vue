<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  name: string
  src?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const props = withDefaults(defineProps<Props>(), {
  src: '',
  size: 'md',
})

const initials = computed<string>(() => {
  const parts = props.name.trim().split(/\s+/).slice(0, 2)
  return parts
    .map((p) => p.charAt(0).toUpperCase())
    .join('')
    .slice(0, 2)
})

const sizeClass = computed<string>(() => {
  switch (props.size) {
    case 'sm':
      return 'h-7 w-7 text-xs'
    case 'md':
      return 'h-9 w-9 text-sm'
    case 'lg':
      return 'h-12 w-12 text-base'
    case 'xl':
      return 'h-16 w-16 text-lg'
  }
})
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary-100 font-semibold text-primary-700 dark:bg-primary-900/40 dark:text-primary-300"
    :class="sizeClass"
    :title="name"
    aria-hidden="true"
  >
    <img v-if="src" :src="src" :alt="name" class="h-full w-full object-cover" />
    <span v-else>{{ initials }}</span>
  </span>
</template>
