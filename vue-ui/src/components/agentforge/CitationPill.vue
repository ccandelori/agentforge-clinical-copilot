<script setup lang="ts">
import { computed } from 'vue'

import type { Citation } from '@/stores/agentforge'

interface Props {
  citation: Citation
  index: number
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select', id: string): void
}>()

const label = computed<string>(() => {
  return `[${props.index + 1}] ${props.citation.source}`
})

function onClick(): void {
  emit('select', props.citation.id)
}
</script>

<template>
  <button
    type="button"
    class="inline-flex max-w-full items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-primary-700/60 dark:bg-primary-900/30 dark:text-primary-300 dark:hover:bg-primary-900/50"
    :title="citation.excerpt"
    @click="onClick"
  >
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      class="h-3 w-3 shrink-0"
      aria-hidden="true"
    >
      <path d="M9 12h6M9 8h6M9 16h4" />
      <path d="M5 4h11l4 4v12H5z" />
    </svg>
    <span class="truncate">{{ label }}</span>
  </button>
</template>
