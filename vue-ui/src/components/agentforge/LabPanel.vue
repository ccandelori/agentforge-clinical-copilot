<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import DocumentViewer from '@/components/DocumentViewer.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import type {
  LabAbnormalFlag,
  LabExtraction,
} from '@/composables/parseLabExtraction'
import type { PageBBox } from '@/types/citation'

interface Props {
  extraction: LabExtraction
}

const props = defineProps<Props>()

const route = useRoute()
const showSource = ref<boolean>(false)

/**
 * Walk the lab values and collect every bbox-bearing citation. Used as
 * the overlay set passed into the DocumentViewer when the user opens
 * the source-document modal. Mirrors the equivalent bbox aggregator on
 * `ExtractionPanel.vue` (intake side) — the two panels intentionally
 * use the same trust-artifact pattern so a clinician's mental model
 * carries across.
 */
const bboxes = computed<readonly PageBBox[]>(() => {
  const out: PageBBox[] = []
  for (const v of props.extraction.values) {
    if (v.citation.pageBbox !== undefined) out.push(v.citation.pageBbox)
  }
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

// Always offer "View source" when we know the document id — the modal
// is useful even with zero bboxes (raw PDF view, useful for verifying
// the extraction or filling gaps the extractor missed). Bboxes count
// in the label ("View source (3)") signals overlay richness; absence
// renders as just "View source".
const canShowSource = computed<boolean>(() => sourceUrl.value !== null)

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

const hasValues = computed<boolean>(() => props.extraction.values.length > 0)
const hasUnsupported = computed<boolean>(
  () => props.extraction.unsupportedFields.length > 0,
)
const hasHeaderMeta = computed<boolean>(
  () =>
    typeof props.extraction.orderingProvider === 'string'
    || typeof props.extraction.accessionNumber === 'string',
)

/**
 * Display label for the closed-set abnormal-flag enum. The wire
 * representation is snake_case (`critical_high`) — render as
 * "Critical high" so the cell reads naturally.
 */
const FLAG_LABELS: Readonly<Record<LabAbnormalFlag, string>> = {
  normal: 'Normal',
  high: 'High',
  low: 'Low',
  critical_high: 'Critical high',
  critical_low: 'Critical low',
  unknown: '—',
}

/**
 * Tone-by-flag classes. Critical flags get the danger palette; high/low
 * use warning; normal is muted; unknown reads as plain ink-muted (it is
 * NOT "normal" — see the AbnormalFlag docstring on the sidecar).
 */
const FLAG_TONES: Readonly<Record<LabAbnormalFlag, string>> = {
  normal: 'bg-success-100 text-success-700 dark:bg-success-700/20 dark:text-success-400',
  high: 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-400',
  low: 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-400',
  critical_high: 'bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-400',
  critical_low: 'bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-400',
  unknown: 'bg-surface-2 text-ink-muted',
}

function flagLabel(flag: LabAbnormalFlag): string {
  return FLAG_LABELS[flag]
}

function flagTone(flag: LabAbnormalFlag): string {
  return FLAG_TONES[flag]
}
</script>

<template>
  <div class="rounded-xl border border-line bg-surface-2 p-3 shadow-card">
    <header class="flex items-center justify-between gap-2">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-ink-muted">
        Extracted from lab report
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
          View source<span v-if="bboxes.length > 0"> ({{ bboxes.length }})</span>
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
      v-if="hasHeaderMeta"
      class="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[11px] text-ink-muted"
    >
      <span v-if="extraction.orderingProvider">
        <span class="font-semibold uppercase tracking-wide">Ordering provider:</span>
        <span class="ml-1 text-ink">{{ extraction.orderingProvider }}</span>
      </span>
      <span v-if="extraction.accessionNumber">
        <span class="font-semibold uppercase tracking-wide">Accession:</span>
        <span class="ml-1 text-ink">{{ extraction.accessionNumber }}</span>
      </span>
    </div>

    <section v-if="hasValues" class="mt-3 overflow-hidden rounded-lg border border-line">
      <table class="w-full border-collapse text-left text-sm">
        <thead class="bg-surface text-[10px] uppercase tracking-wide text-ink-muted">
          <tr>
            <th class="px-2 py-1.5 font-semibold">Test</th>
            <th class="px-2 py-1.5 font-semibold">Value</th>
            <th class="px-2 py-1.5 font-semibold">Reference</th>
            <th class="px-2 py-1.5 font-semibold">Flag</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr
            v-for="(v, idx) in extraction.values"
            :key="`${v.testName}-${idx}`"
            class="bg-surface-2/40"
          >
            <td class="px-2 py-1.5 align-top">
              <div class="font-medium text-ink">{{ v.testName }}</div>
              <div
                v-if="v.loincCode"
                class="text-[10px] uppercase tracking-wide text-ink-muted"
              >
                LOINC {{ v.loincCode }}
              </div>
            </td>
            <td class="px-2 py-1.5 align-top text-ink">
              <span class="font-medium">{{ v.value }}</span>
              <span v-if="v.unit" class="ml-1 text-ink-muted">{{ v.unit }}</span>
            </td>
            <td class="px-2 py-1.5 align-top text-ink-muted">
              {{ v.referenceRange ?? '—' }}
            </td>
            <td class="px-2 py-1.5 align-top">
              <span
                class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
                :class="flagTone(v.abnormalFlag)"
              >
                {{ flagLabel(v.abnormalFlag) }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <p v-else class="mt-3 text-xs italic text-ink-muted">
      No lab values were extracted from this report.
    </p>

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
