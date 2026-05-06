<script setup lang="ts">
import { useFhirResource } from '@/composables/useFhirResource'
import { useAuthStore } from '@/stores/auth'
import AllergiesCard from '@/components/AllergiesCard.vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import PatientHeader from '@/components/PatientHeader.vue'
import ProblemListCard from '@/components/ProblemListCard.vue'

const props = defineProps<{ pid: string }>()

const auth = useAuthStore()
const { status, data, error } = useFhirResource<fhir4.Patient>(
  `/api/fhir/Patient/${encodeURIComponent(props.pid)}`,
)

// Card placeholders for the cards still to come. AllergiesCard
// (T38.4) and ProblemListCard (T38.5) landed; the rest get replaced
// as their subtasks land.
const placeholders: ReadonlyArray<{ title: string; subtask: string }> = [
  { title: 'Medications', subtask: 'T38.6' },
  { title: 'Prescriptions', subtask: 'T38.7' },
  { title: 'Care Team', subtask: 'T38.8' },
  { title: 'Lab Results', subtask: 'T38.9' },
]

async function signOut(): Promise<void> {
  await auth.signOut()
}
</script>

<template>
  <div class="bg-light min-vh-100">
    <nav
      class="navbar navbar-light bg-white border-bottom px-4 py-2 d-flex justify-content-between"
    >
      <RouterLink
        :to="{ name: 'patient-picker' }"
        class="text-decoration-none"
      >
        <i class="bi bi-arrow-left me-1" aria-hidden="true"></i>
        Patients
      </RouterLink>
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
    </nav>

    <div
      v-if="status === 'loading'"
      class="container py-5 d-flex align-items-center text-muted"
    >
      <span
        class="spinner-border spinner-border-sm me-2"
        aria-hidden="true"
      ></span>
      Loading patient…
    </div>
    <div v-else-if="status === 'error'" class="container py-5">
      <div class="alert alert-danger" role="alert">
        <strong>Failed to load patient.</strong>
        {{ error?.message ?? '' }}
      </div>
    </div>
    <template v-else-if="data">
      <PatientHeader :patient="data" />
      <main class="container py-4">
        <AllergiesCard :pid="props.pid" />
        <ProblemListCard :pid="props.pid" />
        <ClinicalCard
          v-for="card in placeholders"
          :key="card.title"
          :title="card.title"
          state="empty"
          collapsible
        >
          <template #empty>
            <div class="text-muted small">
              Pending {{ card.subtask }} —
              {{ card.title.toLowerCase() }} placeholder.
            </div>
          </template>
        </ClinicalCard>
      </main>
    </template>
  </div>
</template>
