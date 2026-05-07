<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'
import { formatFhirDate } from '@/utils/formatDate'

// Prescription history. Same /MedicationRequest endpoint as
// MedicationsCard (T38.6) but client-filters out the active rows
// — those live in MedicationsCard. Surfaces dispenseRequest.
// numberOfRepeatsAllowed (refills) per the T38.7 spec.

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/MedicationRequest?patient=${encodeURIComponent(props.pid)}`,
)

interface PrescriptionRow {
  id: string
  medication: string
  dosageInstruction: string | null
  status: string | null
  authoredOn: string | null
  requester: string | null
  refills: number | null
}

function isMedicationRequest(
  r: fhir4.Resource | undefined,
): r is fhir4.MedicationRequest {
  return (
    r !== undefined
    && (r as fhir4.MedicationRequest).resourceType === 'MedicationRequest'
  )
}

function pickMedicationName(m: fhir4.MedicationRequest): string {
  const cc = m.medicationCodeableConcept
  if (cc !== undefined) {
    const text = cc.text
    if (text !== undefined && text !== '') return text
    const display = cc.coding?.[0]?.display
    if (display !== undefined && display !== '') return display
  }
  const ref = m.medicationReference
  if (ref !== undefined) {
    const display = ref.display
    if (display !== undefined && display !== '') return display
    return '(referenced medication)'
  }
  return '(unknown medication)'
}

const STATUS_BADGE: Record<string, string> = {
  completed: 'bg-success',
  stopped: 'bg-warning text-dark',
  cancelled: 'bg-secondary',
  'on-hold': 'bg-info text-dark',
  draft: 'bg-secondary',
  'entered-in-error': 'bg-secondary',
  unknown: 'bg-secondary',
}

const prescriptions = computed<PrescriptionRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: PrescriptionRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isMedicationRequest(r)) continue
    // Active prescriptions live in MedicationsCard.
    if (r.status === 'active') continue
    if (r.id === undefined) continue
    rows.push({
      id: r.id,
      medication: pickMedicationName(r),
      dosageInstruction: r.dosageInstruction?.[0]?.text ?? null,
      status: r.status ?? null,
      authoredOn: r.authoredOn ?? null,
      requester: r.requester?.display ?? null,
      refills: r.dispenseRequest?.numberOfRepeatsAllowed ?? null,
    })
  }
  rows.sort((a, b) => {
    const ad = a.authoredOn ?? ''
    const bd = b.authoredOn ?? ''
    return bd.localeCompare(ad)
  })
  return rows
})

const cardState = computed<'loading' | 'empty' | 'error' | 'ready'>(() => {
  if (status.value === 'idle' || status.value === 'loading') return 'loading'
  if (status.value === 'error') return 'error'
  if (prescriptions.value.length === 0) return 'empty'
  return 'ready'
})

function statusBadgeClass(s: string | null): string {
  if (s === null) return 'bg-secondary'
  return STATUS_BADGE[s] ?? 'bg-secondary'
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
</script>

<template>
  <ClinicalCard
    title="Prescription history"
    :count="cardState === 'ready' ? prescriptions.length : null"
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
      <div class="text-muted small">No prescription history on file.</div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="p in prescriptions" :key="p.id">
        <div class="d-flex align-items-baseline justify-content-between gap-2">
          <div class="fw-semibold">{{ p.medication }}</div>
          <span
            v-if="p.status !== null"
            class="badge"
            :class="statusBadgeClass(p.status)"
          >
            {{ capitalize(p.status) }}
          </span>
        </div>
        <div v-if="p.dosageInstruction !== null" class="small mt-1">
          {{ p.dosageInstruction }}
        </div>
        <div class="small text-muted mt-1 d-flex flex-wrap gap-3">
          <span v-if="p.authoredOn !== null">
            Prescribed: {{ formatFhirDate(p.authoredOn) }}
          </span>
          <span v-if="p.requester !== null">By: {{ p.requester }}</span>
          <span v-if="p.refills !== null">Refills: {{ p.refills }}</span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
