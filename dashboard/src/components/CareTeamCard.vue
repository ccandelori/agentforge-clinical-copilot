<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'

// Care Team card. Flattens participants across every CareTeam returned
// for the patient. No status filter on the wire because Synthea-
// imported CareTeams come back as "inactive" (tied to past
// encounters); filtering them out would empty the card. Patient-as-
// participant rows are skipped — they're FHIR-correct but noise from
// a clinical-UI standpoint.

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/CareTeam?patient=${encodeURIComponent(props.pid)}`,
)

interface ParticipantRow {
  key: string
  name: string
  role: string
  teamName: string | null
}

function isCareTeam(r: fhir4.Resource | undefined): r is fhir4.CareTeam {
  return r !== undefined && (r as fhir4.CareTeam).resourceType === 'CareTeam'
}

function pickRole(p: fhir4.CareTeamParticipant): string {
  const role = p.role?.[0]
  if (role === undefined) return '(unspecified role)'
  const text = role.text
  if (text !== undefined && text !== '') return text
  const display = role.coding?.[0]?.display
  if (display !== undefined && display !== '') return display
  return '(unspecified role)'
}

function pickMemberName(p: fhir4.CareTeamParticipant): string {
  const display = p.member?.display
  if (display !== undefined && display !== '') return display
  return '(unknown member)'
}

// SNOMED 116154003 = "Patient" — used by Synthea-style data; in
// production OpenEMR the role is more often expressed via a
// Patient/<id> reference. Cover both signals.
function isPatientParticipant(p: fhir4.CareTeamParticipant): boolean {
  const ref = p.member?.reference
  if (ref !== undefined && ref.startsWith('Patient/')) return true
  for (const role of p.role ?? []) {
    if (role.text === 'Patient') return true
    for (const coding of role.coding ?? []) {
      if (coding.code === '116154003') return true
      if (coding.display === 'Patient') return true
    }
  }
  return false
}

const participants = computed<ParticipantRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []
  const rows: ParticipantRow[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (!isCareTeam(r)) continue
    const teamName = r.name ?? null
    let i = 0
    for (const p of r.participant ?? []) {
      if (isPatientParticipant(p)) continue
      const role = pickRole(p)
      const name = pickMemberName(p)
      rows.push({
        key: `${r.id ?? 'team'}-${i++}-${name}-${role}`,
        name,
        role,
        teamName,
      })
    }
  }
  // Sort by role then name to cluster like-roled participants.
  rows.sort((a, b) => {
    const r = a.role.localeCompare(b.role)
    if (r !== 0) return r
    return a.name.localeCompare(b.name)
  })
  return rows
})

const cardState = computed<'loading' | 'empty' | 'error' | 'ready'>(() => {
  if (status.value === 'idle' || status.value === 'loading') return 'loading'
  if (status.value === 'error') return 'error'
  if (participants.value.length === 0) return 'empty'
  return 'ready'
})
</script>

<template>
  <ClinicalCard
    title="Care Team"
    :count="cardState === 'ready' ? participants.length : null"
    :state="cardState"
    :error="error"
    collapsible
  >
    <template #loading>
      <div class="placeholder-glow" aria-hidden="true">
        <p class="placeholder col-7 mb-2"></p>
        <p class="placeholder col-5"></p>
      </div>
    </template>

    <template #empty>
      <div class="text-muted small">No care team assigned.</div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="p in participants" :key="p.key">
        <div class="fw-semibold">{{ p.name }}</div>
        <div class="small text-muted">
          {{ p.role
          }}<span v-if="p.teamName !== null"> · {{ p.teamName }}</span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
