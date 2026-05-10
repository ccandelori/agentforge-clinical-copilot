<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import DocumentViewer from '@/components/DocumentViewer.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import { humanizeFieldName } from '@/composables/humanizeFieldName'
import type { IntakeExtraction } from '@/composables/useAgentTurn'
import {
  useCommitExtraction,
  type CommitItem,
  type CommitItemKind,
} from '@/composables/useCommitExtraction'
import { invalidatePatientCache } from '@/composables/usePatient'
import type { PageBBox } from '@/types/citation'

interface Props {
  extraction: IntakeExtraction
  /**
   * Optional QR id surfaced by the upstream `useAgentTurn` call (the
   * sidecar's `persisted_resource_id`). Threaded through to the BFF
   * so chart rows carry an audit trail back to the extraction-time
   * QuestionnaireResponse. Not load-bearing for the write to
   * succeed — the PHP side stores it as documentary metadata only.
   */
  questionnaireResponseId?: string
}

const props = defineProps<Props>()

/**
 * Emitted after a successful commit so the parent can decide how to
 * react (e.g. close the panel, surface a toast, scroll the cards
 * into view). Payload is the count of rows actually written —
 * the panel itself handles patient-cache invalidation so the cards
 * will refresh on next mount even if the parent ignores the event.
 */
const emit = defineEmits<{
  (e: 'committed', count: number): void
}>()

const route = useRoute()
const showSource = ref<boolean>(false)

/**
 * Walk the extraction and collect every bbox-bearing citation. Used
 * as the overlay set passed into the DocumentViewer when the user
 * opens the source-document modal.
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

// ---------------------------------------------------------------------------
// Per-row commit selection (Gap 2)
//
// Each promotable row gets a checkbox keyed by a synthesized id
// (`${kind}-${index}`). Default-on: the safety property here is the
// explicit Commit click, NOT the per-row checkbox state. Clinicians
// uncheck rows they don't want; auto-checking saves them N clicks
// when the extraction is clean (the common case).
// ---------------------------------------------------------------------------

interface RowKey {
  readonly kind: CommitItemKind
  readonly index: number
}

function rowId(key: RowKey): string {
  return `${key.kind}-${key.index}`
}

// Reactive map: row id → checked. Initialised lazily via `isChecked`.
const checked = reactive<Record<string, boolean>>({})
// Persisted "this row already landed in the chart" set — drives the
// dim+disable state after a successful commit so the user can't
// re-commit the same rows accidentally.
const committedIds = reactive<Set<string>>(new Set())

function isChecked(key: RowKey): boolean {
  const id = rowId(key)
  // Default-checked: lazy-initialise to true so the first read sets
  // the reactive entry and the commit pass picks it up uniformly
  // with explicit toggles.
  if (!(id in checked)) {
    checked[id] = true
  }
  return checked[id]
}

function setChecked(key: RowKey, value: boolean): void {
  checked[rowId(key)] = value
}

function isCommitted(key: RowKey): boolean {
  return committedIds.has(rowId(key))
}

/**
 * Build the CommitItem list from the currently-checked rows. Uses
 * the writer's column conventions:
 *
 * - allergy: title=substance, details="reaction (severity)"
 * - medical_problem: title=condition, details=null
 * - medication: title=name, details="dose / frequency"
 * - family_history: title="relative: condition", details=null
 *
 * The PHP writer accepts a non-empty title and uses details
 * verbatim when present, so we drop `details` entirely on rows that
 * have no meaningful secondary content rather than emitting empty
 * strings.
 */
const selectedItems = computed<readonly CommitItem[]>(() => {
  const out: CommitItem[] = []

  props.extraction.allergies.forEach((a, idx) => {
    const key: RowKey = { kind: 'allergy', index: idx }
    if (!isChecked(key) || isCommitted(key)) return
    const detailParts: string[] = []
    if (a.reaction !== undefined && a.reaction.length > 0) detailParts.push(a.reaction)
    if (a.severity !== undefined && a.severity.length > 0) detailParts.push(`(${a.severity})`)
    const item: CommitItem = {
      kind: 'allergy',
      title: a.substance,
      ...(detailParts.length > 0 ? { details: detailParts.join(' ') } : {}),
    }
    out.push(item)
  })

  props.extraction.medications.forEach((m, idx) => {
    const key: RowKey = { kind: 'medication', index: idx }
    if (!isChecked(key) || isCommitted(key)) return
    const detailParts: string[] = []
    if (m.dose !== undefined && m.dose.length > 0) detailParts.push(m.dose)
    if (m.frequency !== undefined && m.frequency.length > 0) detailParts.push(m.frequency)
    const item: CommitItem = {
      kind: 'medication',
      title: m.name,
      ...(detailParts.length > 0 ? { details: detailParts.join(' / ') } : {}),
    }
    out.push(item)
  })

  // Family history rolls into "medical_problem" in the chart? No —
  // OpenEMR has a distinct `family_history` lists.type. The rows we
  // emit go into the family-history list which the standard chart
  // tabs already render.
  props.extraction.familyHistory.forEach((f, idx) => {
    const key: RowKey = { kind: 'family_history', index: idx }
    if (!isChecked(key) || isCommitted(key)) return
    out.push({
      kind: 'family_history',
      title: `${f.relative}: ${f.condition}`,
    })
  })

  // The intake form has no top-level "problems" list — but the
  // unsupported_fields and chief_concern shape don't promote
  // cleanly to a `medical_problem`. Reserve the kind for a future
  // worker enhancement (e.g. an "active conditions" section). For
  // now no items emit `medical_problem`, so the writer's enum
  // surface stays correct without a dead code path.

  return out
})

const selectedCount = computed<number>(() => selectedItems.value.length)

const totalPromotableRows = computed<number>(() => {
  return (
    props.extraction.allergies.length
    + props.extraction.medications.length
    + props.extraction.familyHistory.length
  )
})

const hasPromotableRows = computed<boolean>(
  () => totalPromotableRows.value > 0,
)

const { commit, status: commitStatus, error: commitError } = useCommitExtraction()
const isCommitting = computed<boolean>(() => commitStatus.value === 'loading')

async function onCommit(): Promise<void> {
  const patientUuid = route.params.id
  if (typeof patientUuid !== 'string' || patientUuid.length === 0) {
    return
  }
  if (selectedCount.value === 0 || isCommitting.value) {
    return
  }

  // Snapshot the row ids we're about to commit so the success path
  // can mark them committed even if the user toggles checkboxes
  // mid-flight.
  const promotedKeys: RowKey[] = []
  props.extraction.allergies.forEach((_, idx) => {
    const key: RowKey = { kind: 'allergy', index: idx }
    if (isChecked(key) && !isCommitted(key)) promotedKeys.push(key)
  })
  props.extraction.medications.forEach((_, idx) => {
    const key: RowKey = { kind: 'medication', index: idx }
    if (isChecked(key) && !isCommitted(key)) promotedKeys.push(key)
  })
  props.extraction.familyHistory.forEach((_, idx) => {
    const key: RowKey = { kind: 'family_history', index: idx }
    if (isChecked(key) && !isCommitted(key)) promotedKeys.push(key)
  })

  try {
    const result = await commit({
      patientUuid,
      items: selectedItems.value,
      ...(props.questionnaireResponseId !== undefined
        ? { questionnaireResponseId: props.questionnaireResponseId }
        : {}),
      documentId: String(props.extraction.documentId),
    })
    for (const key of promotedKeys) {
      committedIds.add(rowId(key))
    }
    // Drop the cached patient bundle so the next render re-fetches
    // the cards (Allergies / Problems / Medications) from FHIR.
    invalidatePatientCache(patientUuid)
    emit('committed', result.count)
  } catch {
    // Error surfaces via the commitError ref the template renders;
    // promotedKeys stay un-committed so the user can retry.
  }
}
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
        “{{ extraction.chiefConcernCitation.quoteOrValue }}”
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
          <dt class="shrink-0 text-ink-muted">{{ humanizeFieldName(d.field) }}:</dt>
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
          v-for="(m, idx) in extraction.medications"
          :key="`${m.name}-${m.dose ?? ''}-${m.frequency ?? ''}`"
          class="flex flex-wrap items-baseline gap-x-2"
          :class="isCommitted({ kind: 'medication', index: idx }) ? 'opacity-50' : ''"
        >
          <label
            class="inline-flex items-center gap-2 cursor-pointer"
            :class="isCommitted({ kind: 'medication', index: idx }) ? 'cursor-not-allowed' : ''"
          >
            <input
              type="checkbox"
              :checked="isChecked({ kind: 'medication', index: idx })"
              :disabled="isCommitted({ kind: 'medication', index: idx })"
              class="h-3.5 w-3.5 rounded border-line text-primary-600 focus:ring-primary-500"
              :aria-label="`Commit ${m.name} to chart`"
              @change="setChecked({ kind: 'medication', index: idx }, ($event.target as HTMLInputElement).checked)"
            />
            <span class="font-medium">{{ m.name }}</span>
          </label>
          <span v-if="m.dose" class="text-ink-muted">{{ m.dose }}</span>
          <span v-if="m.frequency" class="text-ink-muted">· {{ m.frequency }}</span>
          <span
            v-if="isCommitted({ kind: 'medication', index: idx })"
            class="text-[10px] uppercase text-success-600 dark:text-success-400"
          >· committed</span>
        </li>
      </ul>
    </section>

    <section v-if="hasAllergies" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Allergies
      </h5>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="(a, idx) in extraction.allergies"
          :key="`${a.substance}-${a.reaction ?? ''}`"
          class="flex flex-wrap items-baseline gap-x-2"
          :class="isCommitted({ kind: 'allergy', index: idx }) ? 'opacity-50' : ''"
        >
          <label
            class="inline-flex items-center gap-2 cursor-pointer"
            :class="isCommitted({ kind: 'allergy', index: idx }) ? 'cursor-not-allowed' : ''"
          >
            <input
              type="checkbox"
              :checked="isChecked({ kind: 'allergy', index: idx })"
              :disabled="isCommitted({ kind: 'allergy', index: idx })"
              class="h-3.5 w-3.5 rounded border-line text-primary-600 focus:ring-primary-500"
              :aria-label="`Commit ${a.substance} allergy to chart`"
              @change="setChecked({ kind: 'allergy', index: idx }, ($event.target as HTMLInputElement).checked)"
            />
            <span class="font-medium">{{ a.substance }}</span>
          </label>
          <span v-if="a.reaction" class="text-ink-muted">— {{ a.reaction }}</span>
          <span v-if="a.severity" class="text-ink-muted">({{ a.severity }})</span>
          <span
            v-if="isCommitted({ kind: 'allergy', index: idx })"
            class="text-[10px] uppercase text-success-600 dark:text-success-400"
          >· committed</span>
        </li>
      </ul>
    </section>

    <section v-if="hasFamilyHistory" class="mt-3">
      <h5 class="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Family history
      </h5>
      <ul class="mt-1 flex flex-col gap-0.5 text-sm text-ink">
        <li
          v-for="(f, idx) in extraction.familyHistory"
          :key="`${f.relative}-${f.condition}`"
          class="flex flex-wrap items-baseline gap-x-2"
          :class="isCommitted({ kind: 'family_history', index: idx }) ? 'opacity-50' : ''"
        >
          <label
            class="inline-flex items-center gap-2 cursor-pointer"
            :class="isCommitted({ kind: 'family_history', index: idx }) ? 'cursor-not-allowed' : ''"
          >
            <input
              type="checkbox"
              :checked="isChecked({ kind: 'family_history', index: idx })"
              :disabled="isCommitted({ kind: 'family_history', index: idx })"
              class="h-3.5 w-3.5 rounded border-line text-primary-600 focus:ring-primary-500"
              :aria-label="`Commit ${f.relative} ${f.condition} family history to chart`"
              @change="setChecked({ kind: 'family_history', index: idx }, ($event.target as HTMLInputElement).checked)"
            />
            <span class="font-medium">{{ f.relative }}:</span>
          </label>
          <span class="text-ink">{{ f.condition }}</span>
          <span
            v-if="isCommitted({ kind: 'family_history', index: idx })"
            class="text-[10px] uppercase text-success-600 dark:text-success-400"
          >· committed</span>
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

    <!--
      Commit-to-chart footer (Gap 2). Visible only when there's at
      least one promotable row (allergies, medications, or family
      history); demographics and chief concern aren't promotable
      because OpenEMR has no `lists` row type for them and the
      structured demographic fields go through a different
      controller (out of scope for this drop).

      The button label includes the live selection count so the
      clinician knows exactly how many rows the click will land. A
      committed-everything state collapses the button into a
      neutral "All committed" affordance.
    -->
    <footer
      v-if="hasPromotableRows"
      class="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3"
    >
      <p class="text-[11px] text-ink-muted">
        <template v-if="selectedCount > 0">
          {{ selectedCount }} row{{ selectedCount === 1 ? '' : 's' }} selected to commit
        </template>
        <template v-else-if="committedIds.size > 0 && committedIds.size === totalPromotableRows">
          All rows committed to chart
        </template>
        <template v-else>
          Select rows to commit to chart
        </template>
      </p>
      <BaseButton
        v-if="selectedCount > 0 || commitError !== null"
        size="sm"
        variant="primary"
        :disabled="selectedCount === 0 || isCommitting"
        :loading="isCommitting"
        :aria-label="`Commit ${selectedCount} selected row(s) to chart`"
        @click="onCommit"
      >
        Commit selected to chart
      </BaseButton>
    </footer>

    <p
      v-if="commitError !== null"
      class="mt-2 text-[11px] text-danger-600 dark:text-danger-400"
      role="alert"
    >
      {{ commitError.message }}
    </p>
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
