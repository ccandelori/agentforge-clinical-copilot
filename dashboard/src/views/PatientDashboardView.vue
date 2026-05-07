<script setup lang="ts">
import { useFhirResource } from '@/composables/useFhirResource'
import { useAuthStore } from '@/stores/auth'
import AllergiesCard from '@/components/AllergiesCard.vue'
import CareTeamCard from '@/components/CareTeamCard.vue'
import LabResultsCard from '@/components/LabResultsCard.vue'
import MedicationsCard from '@/components/MedicationsCard.vue'
import PatientHeader from '@/components/PatientHeader.vue'
import PrescriptionsCard from '@/components/PrescriptionsCard.vue'
import ProblemListCard from '@/components/ProblemListCard.vue'
import VitalsStrip from '@/components/VitalsStrip.vue'

const props = defineProps<{ pid: string }>()

const auth = useAuthStore()
const { status, data, error } = useFhirResource<fhir4.Patient>(
  `/api/fhir/Patient/${encodeURIComponent(props.pid)}`,
)

// All chart cards (T38.4–T38.9) landed. AgentForge drawer (T38.10)
// is the next piece, but it lives at the page edge — not in this
// vertical card stack.

async function signOut(): Promise<void> {
  await auth.signOut()
}
</script>

<template>
  <div class="bg-body-tertiary min-vh-100">
    <nav
      class="navbar bg-body border-bottom px-4 py-2 d-flex justify-content-between"
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
      <VitalsStrip :pid="props.pid" />
      <main class="container py-4">
        <AllergiesCard :pid="props.pid" />
        <ProblemListCard :pid="props.pid" />
        <MedicationsCard :pid="props.pid" />
        <PrescriptionsCard :pid="props.pid" />
        <LabResultsCard :pid="props.pid" />
        <CareTeamCard :pid="props.pid" />
      </main>
    </template>
  </div>
</template>
