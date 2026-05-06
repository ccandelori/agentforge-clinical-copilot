<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'

// First chart card built against <ClinicalCard>. T38.5–T38.9 follow
// the same shape: take a pid, fetch a search-result Bundle via
// useFhirResource, project resources into row objects, and pass the
// derived state to <ClinicalCard>. Sort: criticality high → low →
// unable-to-assess, then recordedDate desc.

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/AllergyIntolerance?patient=${encodeURIComponent(props.pid)}`,
)

interface AllergyRow {
  id: string
  substance: string
  reactions: string[]
  severity: 'mild' | 'moderate' | 'severe' | null
  criticality: 'high' | 'low' | 'unable-to-assess' | null
  clinicalStatus: string | null
  recordedDate: string | null
}

function isAllergyIntolerance(
  r: fhir4.Resource | undefined,
): r is fhir4.AllergyIntolerance {
  return (
    r !== undefined
    && (r as fhir4.AllergyIntolerance).resourceType === 'AllergyIntolerance'
  )
}

function pickSubstance(a: fhir4.AllergyIntolerance): string {
  const text = a.code?.text
  if (text !== undefined && text !== '') return text
  const display = a.code?.coding?.[0]?.display
  if (display !== undefined && display !== '') return display
  return '(unknown substance)'
}

function pickReactions(a: fhir4.AllergyIntolerance): string[] {
  const out: string[] = []
  for (const r of a.reaction ?? []) {
    for (const m of r.manifestation ?? []) {
      const text = m.text ?? m.coding?.[0]?.display
      if (text !== undefined && text !== '') out.push(text)
    }
  }
  return out
}

const SEVERITY_RANK: Record<string, number> = {
  severe: 0,
  moderate: 1,
  mild: 2,
}

function pickWorstSeverity(
  a: fhir4.AllergyIntolerance,
): 'mild' | 'moderate' | 'severe' | null {
  let best: 'mild' | 'moderate' | 'severe' | null = null
  for (const r of a.reaction ?? []) {
    const sev = r.severity
    if (sev === undefined) continue
    if (best === null || SEVERITY_RANK[sev]! < SEVERITY_RANK[best]!) {
      best = sev
    }
  }
  return best
}

function pickClinicalStatus(a: fhir4.AllergyIntolerance): string | null {
  return a.clinicalStatus?.coding?.[0]?.code ?? null
}

const CRIT_RANK: Record<string, number> = {
  high: 0,
  low: 1,
  'unable-to-assess': 2,
}

const allergies = computed<AllergyRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: AllergyRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isAllergyIntolerance(r)) continue
    if (r.id === undefined) continue
    rows.push({
      id: r.id,
      substance: pickSubstance(r),
      reactions: pickReactions(r),
      severity: pickWorstSeverity(r),
      criticality: r.criticality ?? null,
      clinicalStatus: pickClinicalStatus(r),
      recordedDate: r.recordedDate ?? null,
    })
  }
  rows.sort((a, b) => {
    const ar = a.criticality !== null ? (CRIT_RANK[a.criticality] ?? 99) : 99
    const br = b.criticality !== null ? (CRIT_RANK[b.criticality] ?? 99) : 99
    if (ar !== br) return ar - br
    const ad = a.recordedDate ?? ''
    const bd = b.recordedDate ?? ''
    // Lexicographic compare on ISO-8601 = chronological compare.
    return bd.localeCompare(ad)
  })
  return rows
})

const cardState = computed<'loading' | 'empty' | 'error' | 'ready'>(() => {
  if (status.value === 'idle' || status.value === 'loading') return 'loading'
  if (status.value === 'error') return 'error'
  if (allergies.value.length === 0) return 'empty'
  return 'ready'
})

function severityBadgeClass(
  severity: 'mild' | 'moderate' | 'severe' | null,
): string {
  switch (severity) {
    case 'severe':
      return 'bg-danger'
    case 'moderate':
      return 'bg-warning text-dark'
    case 'mild':
      return 'bg-success'
    default:
      return 'bg-secondary'
  }
}

function clinicalStatusLabel(s: string | null): string {
  if (s === null || s === '') return ''
  return s.charAt(0).toUpperCase() + s.slice(1)
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

function formatRecordedDate(iso: string | null): string {
  if (iso === null) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : dateFormatter.format(d)
}
</script>

<template>
  <ClinicalCard
    title="Allergies"
    :count="cardState === 'ready' ? allergies.length : null"
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
      <div class="d-flex align-items-center small">
        <span
          class="badge rounded-pill bg-success me-2"
          aria-label="No known allergies"
          >NKA</span
        >
        <span class="text-muted">No known allergies on file.</span>
      </div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="a in allergies" :key="a.id">
        <div class="d-flex align-items-baseline justify-content-between gap-2">
          <div class="fw-semibold">
            {{ a.substance }}
            <span
              v-if="a.criticality === 'high'"
              class="badge bg-danger ms-2"
              title="High criticality"
            >
              ⚠ HIGH
            </span>
          </div>
          <span
            v-if="a.severity !== null"
            class="badge"
            :class="severityBadgeClass(a.severity)"
          >
            {{ a.severity }}
          </span>
        </div>
        <div v-if="a.reactions.length > 0" class="small text-muted mt-1">
          Reactions: {{ a.reactions.join(', ') }}
        </div>
        <div class="small text-muted mt-1 d-flex flex-wrap gap-3">
          <span v-if="a.clinicalStatus !== null">
            Status: {{ clinicalStatusLabel(a.clinicalStatus) }}
          </span>
          <span v-if="a.recordedDate !== null">
            Recorded: {{ formatRecordedDate(a.recordedDate) }}
          </span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
