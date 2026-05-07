<script setup lang="ts">
import { computed } from 'vue'

import type { IntakeExtraction } from '@/composables/useAgentTurn'

interface Props {
  extraction: IntakeExtraction
}

const props = defineProps<Props>()

const confidencePct = computed<number>(() => {
  return Math.round(props.extraction.extractionConfidence * 100)
})

const confidenceTone = computed<string>(() => {
  const pct = confidencePct.value
  if (pct >= 80) {
    return 'bg-success-100 text-success-700 dark:bg-success-700/20 dark:text-success-400'
  }
  if (pct >= 60) {
    return 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-400'
  }
  return 'bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-400'
})

const hasDemographics = computed<boolean>(
  () => props.extraction.demographics.length > 0,
)
const hasMedications = computed<boolean>(
  () => props.extraction.medications.length > 0,
)
const hasAllergies = computed<boolean>(
  () => props.extraction.allergies.length > 0,
)
const hasFamilyHistory = computed<boolean>(
  () => props.extraction.familyHistory.length > 0,
)
const hasUnsupported = computed<boolean>(
  () => props.extraction.unsupportedFields.length > 0,
)
</script>

<template>
  <div class="rounded-xl border border-line bg-surface-2 p-3 shadow-card">
    <header class="flex items-center justify-between gap-2">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Extracted from intake form
      </h4>
      <span
        class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
        :class="confidenceTone"
      >
        {{ confidencePct }}% confidence
      </span>
    </header>

    <div
      v-if="extraction.chiefConcern"
      class="mt-3 rounded-lg border border-line bg-surface p-2.5"
    >
      <div class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Chief concern
      </div>
      <p class="mt-1 text-sm leading-relaxed text-ink">
        {{ extraction.chiefConcern }}
      </p>
      <p
        v-if="extraction.chiefConcernCitation"
        class="mt-1 text-[11px] italic text-ink-muted"
      >
        “{{ extraction.chiefConcernCitation.evidenceText }}”
        <span class="not-italic">— {{ extraction.chiefConcernCitation.pageOrSection }}</span>
      </p>
    </div>

    <section v-if="hasDemographics" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Demographics
      </h5>
      <dl class="mt-1 grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
        <div
          v-for="d in extraction.demographics"
          :key="`${d.field}-${d.value}`"
          class="flex items-baseline gap-2 text-sm"
        >
          <dt class="shrink-0 text-ink-muted">{{ d.field }}:</dt>
          <dd class="text-ink">{{ d.value }}</dd>
        </div>
      </dl>
    </section>

    <section v-if="hasMedications" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Medications
      </h5>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="m in extraction.medications"
          :key="`${m.name}-${m.dose ?? ''}-${m.frequency ?? ''}`"
          class="flex flex-wrap items-baseline gap-x-2"
        >
          <span class="font-medium">{{ m.name }}</span>
          <span v-if="m.dose" class="text-ink-muted">{{ m.dose }}</span>
          <span v-if="m.frequency" class="text-ink-muted">· {{ m.frequency }}</span>
        </li>
      </ul>
    </section>

    <section v-if="hasAllergies" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Allergies
      </h5>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="a in extraction.allergies"
          :key="`${a.substance}-${a.reaction ?? ''}`"
          class="flex flex-wrap items-baseline gap-x-2"
        >
          <span class="font-medium">{{ a.substance }}</span>
          <span v-if="a.reaction" class="text-ink-muted">— {{ a.reaction }}</span>
          <span v-if="a.severity" class="text-ink-muted">({{ a.severity }})</span>
        </li>
      </ul>
    </section>

    <section v-if="hasFamilyHistory" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Family history
      </h5>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="f in extraction.familyHistory"
          :key="`${f.relative}-${f.condition}`"
          class="flex flex-wrap items-baseline gap-x-2"
        >
          <span class="font-medium">{{ f.relative }}:</span>
          <span class="text-ink">{{ f.condition }}</span>
        </li>
      </ul>
    </section>

    <section
      v-if="hasUnsupported"
      class="mt-3 rounded-lg border border-warning-300/70 bg-warning-50 p-2.5 dark:border-warning-700/50 dark:bg-warning-900/20"
    >
      <div class="text-[10px] font-semibold uppercase tracking-wide text-warning-700 dark:text-warning-400">
        Needs your review
      </div>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="(f, idx) in extraction.unsupportedFields"
          :key="`${idx}-${f}`"
          class="text-warning-700 dark:text-warning-400"
        >
          {{ f }}
        </li>
      </ul>
    </section>
  </div>
</template>
