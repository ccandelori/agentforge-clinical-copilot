<script setup lang="ts">
import { computed } from 'vue'

// Sticky identity bar at the top of /patient/:pid. Pure presentation —
// the parent view owns the FHIR fetch and passes the resolved Patient
// down. Keeps the header reusable (e.g. inside the AgentForge drawer's
// per-patient context preview later).

const props = defineProps<{
  patient: fhir4.Patient
}>()

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

function parseBirthDate(d: string | undefined): Date | null {
  if (d === undefined || d === '') return null
  const parts = d.split('-').map((p) => Number(p))
  if (parts.length !== 3 || parts.some((p) => Number.isNaN(p))) return null
  const [year, month, day] = parts as [number, number, number]
  return new Date(year, month - 1, day)
}

const fullName = computed<string>(() => {
  const names = props.patient.name ?? []
  const chosen = names.find((n) => n.use === 'official') ?? names[0]
  if (!chosen) return '(unknown)'
  const parts: string[] = []
  if (chosen.given !== undefined && chosen.given.length > 0) {
    parts.push(chosen.given.join(' '))
  }
  if (chosen.family !== undefined && chosen.family !== '') {
    parts.push(chosen.family)
  }
  return parts.length > 0 ? parts.join(' ') : '(unknown)'
})

const dob = computed<string>(() => {
  const date = parseBirthDate(props.patient.birthDate)
  if (date === null) return '—'
  return dateFormatter.format(date)
})

const age = computed<number | null>(() => {
  const date = parseBirthDate(props.patient.birthDate)
  if (date === null) return null
  const now = new Date()
  let years = now.getFullYear() - date.getFullYear()
  const m = now.getMonth() - date.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < date.getDate())) {
    years -= 1
  }
  return years
})

const sex = computed<string>(() => {
  const g = props.patient.gender
  if (g === undefined) return '—'
  return g.charAt(0).toUpperCase() + g.slice(1)
})

const mrn = computed<string>(() => {
  const ids = props.patient.identifier ?? []
  const mr = ids.find((i) =>
    i.type?.coding?.some((c) => c.code === 'MR'),
  )
  return mr?.value ?? '—'
})

// FHIR R4: Patient.active is optional. Per the resource definition,
// "if a value is not provided, the resource is interpreted to be
// active." Only render the badge when active is *explicitly* false.
const isActive = computed<boolean>(() => props.patient.active !== false)
</script>

<template>
  <header
    class="patient-header bg-white border-bottom shadow-sm py-3 px-4 sticky-top"
    style="top: 0; z-index: 1020"
    aria-label="Patient identity"
  >
    <div class="d-flex align-items-baseline flex-wrap gap-3">
      <h1
        class="h4 mb-0"
        :class="{ 'text-muted text-decoration-line-through': !isActive }"
      >
        {{ fullName }}
      </h1>
      <span
        v-if="!isActive"
        class="badge bg-secondary"
        title="Patient record marked inactive"
      >
        Inactive
      </span>
      <div class="d-flex flex-wrap small text-muted gap-3 mb-0">
        <div>
          <span class="fw-semibold">DOB:</span>
          {{ dob }}
          <span v-if="age !== null">({{ age }} y)</span>
        </div>
        <div>
          <span class="fw-semibold">Sex:</span>
          {{ sex }}
        </div>
        <div>
          <span class="fw-semibold">MRN:</span>
          <code class="ms-1">{{ mrn }}</code>
        </div>
      </div>
    </div>
  </header>
</template>
