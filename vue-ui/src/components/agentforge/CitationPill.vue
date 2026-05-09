<script setup lang="ts">
import { computed } from 'vue'

import { citationKey, type Citation } from '@/composables/useAgentTurn'

interface Props {
  citation: Citation
  index: number
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'select', id: string): void
}>()

// Compact source-type label that fits in a pill — keeps the pill text
// honest about the W2 source ontology (chart vs scanned vs guideline)
// without screaming "OPENEMR_RECORD" at the clinician.
const sourceTypeLabel = computed<string>(() => {
  switch (props.citation.source_type) {
    case 'openemr_record':
      return 'Chart'
    case 'guideline':
      return 'Guideline'
    case 'lab_pdf':
      return 'Lab PDF'
    case 'intake_form':
      return 'Intake'
  }
})

const label = computed<string>(() => {
  const idx = props.index + 1
  const tag = sourceTypeLabel.value
  const id = props.citation.source_id
  const loc = props.citation.page_or_section
  // ``[1] Chart 116 · 2026-04-12`` — locator is omitted cleanly when
  // the BFF couldn't supply one (chart records with no date,
  // fallback path).
  return loc !== null ? `[${idx}] ${tag} ${id} · ${loc}` : `[${idx}] ${tag} ${id}`
})

// quote_or_value is the verbatim ground for the claim — surface it as
// the pill's tooltip so a hover/long-press reveals the trace without
// expanding the citations pane. Empty string when null so the title
// attribute renders nothing rather than "null".
const tooltip = computed<string>(() => props.citation.quote_or_value ?? '')

const targetId = computed<string>(() => citationKey(props.citation))

function onClick(): void {
  emit('select', targetId.value)
}
</script>

<template>
  <button
    type="button"
    class="inline-flex max-w-full items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-primary-700/60 dark:bg-primary-900/30 dark:text-primary-300 dark:hover:bg-primary-900/50"
    :title="tooltip"
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
