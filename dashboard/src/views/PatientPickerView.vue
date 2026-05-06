<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useFhirResource } from '@/composables/useFhirResource'
import { useAuthStore } from '@/stores/auth'

// Implicit-picker step. The signed-in user is an OpenEMR admin/Person
// with no built-in patient context, so the dashboard lands here on
// /, lets the user pick a patient, then navigates to /patient/:pid.

const auth = useAuthStore()
const { status, data, error } = useFhirResource<fhir4.Bundle>(
  '/api/fhir/Patient?_count=20',
)

interface PatientRow {
  id: string
  name: string
  dob: string
  mrn: string
}

function formatName(p: fhir4.Patient): string {
  const names = p.name ?? []
  const chosen = names.find((n) => n.use === 'official') ?? names[0]
  if (!chosen) return '(unknown)'
  const family = chosen.family ?? ''
  const given = chosen.given !== undefined ? chosen.given.join(' ') : ''
  if (family !== '' && given !== '') return `${family}, ${given}`
  if (family !== '') return family
  if (given !== '') return given
  return '(unknown)'
}

function formatMrn(p: fhir4.Patient): string {
  const ids = p.identifier ?? []
  const mr = ids.find((i) => i.type?.coding?.some((c) => c.code === 'MR'))
  return mr?.value ?? '—'
}

// @types/fhir's Bundle.entry.resource is typed as the base `Resource`,
// which doesn't carry the `resourceType` discriminator. Narrow via a
// dedicated guard so the rest of the loop can treat each row as a
// fully-typed Patient.
function isPatient(r: fhir4.Resource | undefined): r is fhir4.Patient {
  return r !== undefined && (r as fhir4.Patient).resourceType === 'Patient'
}

const patients = computed<PatientRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: PatientRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isPatient(r)) continue
    const id = r.id
    if (id === undefined) continue
    rows.push({
      id,
      name: formatName(r),
      dob: r.birthDate ?? '—',
      mrn: formatMrn(r),
    })
  }
  return rows
})

async function signOut(): Promise<void> {
  await auth.signOut()
}
</script>

<template>
  <main class="container py-4" style="max-width: 60rem">
    <header
      class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2"
    >
      <div>
        <h1 class="h3 mb-0">AgentForge Dashboard</h1>
        <p class="text-muted small mb-0">
          Pick a patient to open the chart.
        </p>
      </div>
      <div class="d-flex align-items-center gap-3">
        <span class="small text-muted">
          {{ auth.user?.name ?? auth.user?.sub ?? '' }}
        </span>
        <button
          type="button"
          class="btn btn-outline-secondary btn-sm"
          @click="signOut"
        >
          Sign out
        </button>
      </div>
    </header>

    <div
      v-if="status === 'loading'"
      class="d-flex align-items-center text-muted"
    >
      <span
        class="spinner-border spinner-border-sm me-2"
        aria-hidden="true"
      ></span>
      Loading patients…
    </div>
    <div v-else-if="status === 'error'" class="alert alert-danger" role="alert">
      <strong>Failed to load patients.</strong>
      {{ error?.message ?? '' }}
    </div>
    <div v-else-if="patients.length === 0" class="text-muted small">
      No patients in the roster.
    </div>
    <div v-else class="list-group">
      <RouterLink
        v-for="p in patients"
        :key="p.id"
        :to="{ name: 'patient', params: { pid: p.id } }"
        class="list-group-item list-group-item-action d-flex justify-content-between align-items-center text-decoration-none"
      >
        <div>
          <div class="fw-semibold">{{ p.name }}</div>
          <div class="small text-muted">
            DOB {{ p.dob }} · MRN <code>{{ p.mrn }}</code>
          </div>
        </div>
        <i class="bi bi-chevron-right" aria-hidden="true"></i>
      </RouterLink>
    </div>
  </main>
</template>
