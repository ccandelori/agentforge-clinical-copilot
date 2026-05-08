<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import DocumentViewer from '@/components/DocumentViewer.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import type { IntakeExtraction } from '@/composables/useAgentTurn'
import type { PageBBox } from '@/types/citation'

interface Props {
  extraction: IntakeExtraction
}

const props = defineProps<Props>()

const route = useRoute()
const showSource = ref<boolean>(false)

/**
 * Walk the extraction and collect every bbox-bearing citation. Used as
 * the overlay set passed into the DocumentViewer when the user opens
 * the source-document modal.
 */
const bboxes = computed<readonly PageBBox[]>(() => {
  const out: PageBBox[] = []
  const push = (bbox: PageBBox | undefined): void => {
    if (bbox !== undefined) out.push(bbox)
  }

  push(props.extraction.chiefConcernCitation?.pageBbox)
  for (const d of props.extraction.demographics) push(d.citation.pageBbox)
  for (const m of props.extraction.medications) push(m.citation.pageBbox)
  for (const a of props.extraction.allergies) push(a.citation.pageBbox)
  for (const f of props.extraction.familyHistory) push(f.citation.pageBbox)

  return out
})

const sourceUrl = computed<string | null>(() => {
  const patientUuid = route.params.id
  if (typeof patientUuid !== 'string' || patientUuid.length === 0) {
    return null
  }
  return (
    `/api/agent/document/${props.extraction.documentId}`
    + `?patient_uuid=${encodeURIComponent(patientUuid)}`
  )
})

const canShowSource = computed<boolean>(
  () => sourceUrl.value !== null && bboxes.value.length > 0,
)

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
      <div class="flex items-center gap-2">
        <button
          v-if="canShowSource"
          type="button"
          class="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-[11px] font-medium text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          aria-label="View source document with extracted regions highlighted"
          @click="showSource = true"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            class="h-3 w-3"
            aria-hidden="true"
          >
            <path d="M14 4h6v6" />
            <path d="M10 14 20 4" />
            <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
          </svg>
          View source ({{ bboxes.length }})
        </button>
        <span
          class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
          :class="confidenceTone"
        >
          {{ confidencePct }}% confidence
        </span>
      </div>
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

  <BaseModal
    v-if="canShowSource"
    :open="showSource"
    title="Source document"
    size="xl"
    @update:open="showSource = $event"
  >
    <DocumentViewer
      v-if="showSource && sourceUrl !== null"
      :src="sourceUrl"
      :bboxes="bboxes"
    />
  </BaseModal>
</template>
