<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'
import { formatFhirDate } from '@/utils/formatDate'

// Active medications. T38.6 sources from MedicationRequest filtered
// by status=active because OpenEMR doesn't expose MedicationStatement
// (see DEVIATIONS.md for the rationale). T38.7 reuses the same
// endpoint for the historical statuses (completed/stopped/cancelled).

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/MedicationRequest?patient=${encodeURIComponent(props.pid)}&status=active`,
)

interface MedicationRow {
  id: string
  medication: string
  dosageInstruction: string | null
  status: string | null
  authoredOn: string | null
  requester: string | null
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

const meds = computed<MedicationRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: MedicationRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isMedicationRequest(r)) continue
    // Server may ignore the status filter and return all rows; enforce
    // it client-side to keep the demo card honest.
    if (r.status !== 'active') continue
    if (r.id === undefined) continue
    rows.push({
      id: r.id,
      medication: pickMedicationName(r),
      dosageInstruction: r.dosageInstruction?.[0]?.text ?? null,
      status: r.status ?? null,
      authoredOn: r.authoredOn ?? null,
      requester: r.requester?.display ?? null,
    })
  }
  // Most recently authored first.
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
  if (meds.value.length === 0) return 'empty'
  return 'ready'
})

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
</script>

<template>
  <ClinicalCard
    title="Medications"
    :count="cardState === 'ready' ? meds.length : null"
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
      <div class="text-muted small">No active medications on file.</div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="m in meds" :key="m.id">
        <div class="d-flex align-items-baseline justify-content-between gap-2">
          <div class="fw-semibold">{{ m.medication }}</div>
          <span v-if="m.status !== null" class="badge bg-primary">
            {{ capitalize(m.status) }}
          </span>
        </div>
        <div v-if="m.dosageInstruction !== null" class="small mt-1">
          {{ m.dosageInstruction }}
        </div>
        <div class="small text-muted mt-1 d-flex flex-wrap gap-3">
          <span v-if="m.authoredOn !== null">
            Started: {{ formatFhirDate(m.authoredOn) }}
          </span>
          <span v-if="m.requester !== null">
            Prescribed by: {{ m.requester }}
          </span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
