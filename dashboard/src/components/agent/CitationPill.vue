<script setup lang="ts">
import { computed } from 'vue'

import type { Citation } from '@/composables/useAgentTurn'

// CitationPill — small clickable chip rendered under an assistant
// message. Click emits `select(id)` so the drawer can switch to the
// Citations tab and scroll the matching card into view.
//
// Lifted from `vue-ui/src/components/agentforge/CitationPill.vue`,
// translated from Tailwind to plain Bootstrap 5 + the dashboard's
// design tokens (declared in `dashboard/src/assets/tokens.css`).

interface Props {
  citation: Citation
  /** 1-based label index ("[1] Note 2024-09-12"). */
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
    class="agent-citation-pill btn btn-sm d-inline-flex align-items-center gap-1 rounded-pill px-2 py-1"
    :title="citation.excerpt"
    data-test="agent-citation-pill"
    @click="onClick"
  >
    <i
      class="bi bi-quote agent-citation-pill__icon"
      aria-hidden="true"
    ></i>
    <span class="agent-citation-pill__label text-truncate">{{ label }}</span>
  </button>
</template>

<style scoped>
.agent-citation-pill {
  border: 1px solid var(--bs-primary-border-subtle);
  background-color: var(--bs-primary-bg-subtle);
  color: var(--bs-primary-text-emphasis);
  font-size: 0.75rem;
  font-weight: 500;
  line-height: 1;
  max-width: 100%;
}

.agent-citation-pill:hover,
.agent-citation-pill:focus-visible {
  background-color: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent-hover);
}

.agent-citation-pill__icon {
  font-size: 0.75rem;
  flex-shrink: 0;
}

.agent-citation-pill__label {
  display: inline-block;
  max-width: 14rem;
}
</style>
