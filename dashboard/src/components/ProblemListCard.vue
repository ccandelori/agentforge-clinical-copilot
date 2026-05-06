<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'

// Problem List card. Queries /Condition?patient=&category=problem-
// list-item; also filters client-side because some FHIR servers
// ignore the category parameter and return all Conditions
// (including encounter-diagnosis rows). Sorts active states first
// (active/recurrence/relapse), then inactive states
// (inactive/remission), then resolved; recordedDate desc within a
// group.

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/Condition?patient=${encodeURIComponent(props.pid)}&category=problem-list-item`,
)

type ClinicalStatus =
  | 'active'
  | 'recurrence'
  | 'relapse'
  | 'inactive'
  | 'remission'
  | 'resolved'

interface ConditionRow {
  id: string
  problem: string
  clinicalStatus: ClinicalStatus | null
  verificationStatus: string | null
  onset: string | null
  recordedDate: string | null
}

function isCondition(
  r: fhir4.Resource | undefined,
): r is fhir4.Condition {
  return (
    r !== undefined && (r as fhir4.Condition).resourceType === 'Condition'
  )
}

function isProblemListItem(c: fhir4.Condition): boolean {
  for (const cat of c.category ?? []) {
    for (const coding of cat.coding ?? []) {
      if (coding.code === 'problem-list-item') return true
    }
  }
  return false
}

function pickProblemName(c: fhir4.Condition): string {
  const text = c.code?.text
  if (text !== undefined && text !== '') return text
  const display = c.code?.coding?.[0]?.display
  if (display !== undefined && display !== '') return display
  return '(unknown problem)'
}

function pickClinicalStatus(c: fhir4.Condition): ClinicalStatus | null {
  const code = c.clinicalStatus?.coding?.[0]?.code
  switch (code) {
    case 'active':
    case 'recurrence':
    case 'relapse':
    case 'inactive':
    case 'remission':
    case 'resolved':
      return code
    default:
      return null
  }
}

function pickOnset(c: fhir4.Condition): string | null {
  if (c.onsetDateTime !== undefined) return c.onsetDateTime
  if (c.onsetPeriod?.start !== undefined) return c.onsetPeriod.start
  if (c.onsetString !== undefined) return c.onsetString
  return null
}

const STATUS_RANK: Record<ClinicalStatus, number> = {
  active: 0,
  recurrence: 0,
  relapse: 0,
  inactive: 1,
  remission: 1,
  resolved: 2,
}

const conditions = computed<ConditionRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: ConditionRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isCondition(r)) continue
    if (!isProblemListItem(r)) continue
    if (r.id === undefined) continue
    rows.push({
      id: r.id,
      problem: pickProblemName(r),
      clinicalStatus: pickClinicalStatus(r),
      verificationStatus: r.verificationStatus?.coding?.[0]?.code ?? null,
      onset: pickOnset(r),
      recordedDate: r.recordedDate ?? null,
    })
  }
  rows.sort((a, b) => {
    const ar = a.clinicalStatus !== null ? STATUS_RANK[a.clinicalStatus] : 99
    const br = b.clinicalStatus !== null ? STATUS_RANK[b.clinicalStatus] : 99
    if (ar !== br) return ar - br
    const ad = a.recordedDate ?? ''
    const bd = b.recordedDate ?? ''
    return bd.localeCompare(ad)
  })
  return rows
})

const cardState = computed<'loading' | 'empty' | 'error' | 'ready'>(() => {
  if (status.value === 'idle' || status.value === 'loading') return 'loading'
  if (status.value === 'error') return 'error'
  if (conditions.value.length === 0) return 'empty'
  return 'ready'
})

function statusBadgeClass(s: ClinicalStatus | null): string {
  switch (s) {
    case 'active':
    case 'recurrence':
    case 'relapse':
      return 'bg-primary'
    case 'resolved':
      return 'bg-success'
    case 'inactive':
    case 'remission':
      return 'bg-secondary'
    default:
      return 'bg-secondary'
  }
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

function formatDate(iso: string | null): string {
  if (iso === null) return '—'
  // Date-only strings (YYYY-MM-DD) parse as UTC midnight in modern JS;
  // formatting in a negative-offset locale shifts them back one day.
  // Parse them as local-date instead. Datetime strings (with tz info)
  // fall through to the standard parser.
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number) as [number, number, number]
    return dateFormatter.format(new Date(y, m - 1, d))
  }
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormatter.format(parsed)
}
</script>

<template>
  <ClinicalCard
    title="Problem List"
    :count="cardState === 'ready' ? conditions.length : null"
    :state="cardState"
    :error="error"
    collapsible
  >
    <template #loading>
      <div class="placeholder-glow" aria-hidden="true">
        <p class="placeholder col-7 mb-2"></p>
        <p class="placeholder col-5 mb-2"></p>
        <p class="placeholder col-6"></p>
      </div>
    </template>

    <template #empty>
      <div class="text-muted small">No problems on file.</div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="c in conditions" :key="c.id">
        <div class="d-flex align-items-baseline justify-content-between gap-2">
          <div class="fw-semibold">
            {{ c.problem }}
            <span
              v-if="
                c.verificationStatus !== null &&
                c.verificationStatus !== 'confirmed'
              "
              class="text-muted small ms-2"
            >
              ({{ c.verificationStatus }})
            </span>
          </div>
          <span
            v-if="c.clinicalStatus !== null"
            class="badge"
            :class="statusBadgeClass(c.clinicalStatus)"
          >
            {{ capitalize(c.clinicalStatus) }}
          </span>
        </div>
        <div class="small text-muted mt-1 d-flex flex-wrap gap-3">
          <span v-if="c.onset !== null">
            Onset: {{ formatDate(c.onset) }}
          </span>
          <span v-if="c.recordedDate !== null">
            Recorded: {{ formatDate(c.recordedDate) }}
          </span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
