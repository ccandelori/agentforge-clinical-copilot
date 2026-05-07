<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import { useRouter } from 'vue-router'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSpinner from '@/components/ui/BaseSpinner.vue'
import AllergiesCard from '@/components/patients/dashboard/AllergiesCard.vue'
import EncountersCard from '@/components/patients/dashboard/EncountersCard.vue'
import LabsCard from '@/components/patients/dashboard/LabsCard.vue'
import MedicationsCard from '@/components/patients/dashboard/MedicationsCard.vue'
import PatientHeaderBand from '@/components/patients/dashboard/PatientHeaderBand.vue'
import ProblemListCard from '@/components/patients/dashboard/ProblemListCard.vue'
import VitalsStrip from '@/components/patients/dashboard/VitalsStrip.vue'
import { usePatient } from '@/composables/usePatient'
import { relativeTime } from '@/composables/useRelativeTime'

interface Props {
  readonly id: string
}

const props = defineProps<Props>()

const patientId = toRef(props, 'id')
const {
  patient,
  vitals,
  problems,
  medications,
  allergies,
  encounters,
  labs,
  loading,
  error,
  refresh,
} = usePatient(patientId)

const activeProblemCount = computed<number>(
  () => problems.value.filter((p) => p.status === 'active').length,
)

interface Chip {
  readonly label: string
  readonly value: string
}

const PHARMACIES: readonly string[] = [
  'CVS · Bedford Sq',
  'Walgreens · Main St',
  'Rite Aid · 4th Ave',
  'Walmart · Highway 9',
]

const LANGUAGES: readonly string[] = ['English', 'Spanish', 'Mandarin', 'Portuguese']

const chips = computed<readonly Chip[]>(() => {
  const p = patient.value
  if (!p) return []
  // Deterministic per-patient pseudo-random pick — keeps mock UI stable.
  const seed = Number.parseInt(p.id.replace(/\D/g, ''), 10) || 0
  const pharmacy = PHARMACIES[seed % PHARMACIES.length]!
  const language = LANGUAGES[seed % LANGUAGES.length]!
  const lastEncounter = [...encounters.value].sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime(),
  )[0]

  const items: Chip[] = [
    { label: 'PCP', value: p.pcp ?? 'Unassigned' },
    { label: 'Pharmacy', value: pharmacy },
    { label: 'Language', value: language },
    {
      label: 'Last visit',
      value: lastEncounter ? relativeTime(lastEncounter.date) : '—',
    },
  ]
  if (p.insurance) items.push({ label: 'Insurance', value: p.insurance })
  return items
})

// ---------------- Header actions ----------------
const router = useRouter()

function onNewEncounter(): void {
  if (!patient.value) return
  // Draft id with `new-` prefix tells EncounterEditor to skip the
  // FHIR fetch and build an empty encounter from the patient context.
  const draftId = `new-${Date.now()}`
  router.push({
    name: 'encounter',
    params: { id: draftId },
    query: { patient: patient.value.id },
  })
}

const editOpen = ref<boolean>(false)
const editForm = ref({
  firstName: '',
  lastName: '',
  phone: '',
  email: '',
})
const editSaved = ref<boolean>(false)
let editSavedTimer: number | undefined

function onEditOpen(): void {
  if (!patient.value) return
  editForm.value = {
    firstName: patient.value.firstName,
    lastName: patient.value.lastName,
    phone: patient.value.phone ?? '',
    email: patient.value.email ?? '',
  }
  editSaved.value = false
  editOpen.value = true
}

function onEditSave(): void {
  // Demographics edits aren't wired to a FHIR PATCH yet — show inline
  // confirmation and close. Pre-deadline scope; a real PATCH against
  // /api/fhir/Patient/{id} is the follow-up.
  editSaved.value = true
  if (editSavedTimer !== undefined) window.clearTimeout(editSavedTimer)
  editSavedTimer = window.setTimeout(() => {
    editOpen.value = false
    editSaved.value = false
  }, 1200)
}
</script>

<template>
  <div>
    <!-- Loading: full-screen spinner before any patient is hydrated -->
    <div
      v-if="loading && !patient"
      class="flex min-h-[40vh] items-center justify-center"
    >
      <BaseSpinner size="lg" label="Loading patient" />
    </div>

    <!-- Error -->
    <BaseEmptyState
      v-else-if="error && !patient"
      icon="⚠"
      title="Could not load patient"
      :message="error.message"
    >
      <template #action>
        <BaseButton variant="primary" size="sm" @click="refresh">
          Retry
        </BaseButton>
      </template>
    </BaseEmptyState>

    <!-- Not found -->
    <BaseEmptyState
      v-else-if="!patient"
      icon="?"
      title="Patient not found"
      :message="`No patient with ID ${id}`"
    />

    <!-- Loaded (patient resolved; secondary cards may have partially failed) -->
    <template v-else>
      <!-- Soft warning when patient loaded but one of the cards failed -->
      <div
        v-if="error"
        class="mb-4 flex items-center justify-between gap-3 rounded-lg border border-warning-300 bg-warning-50 px-4 py-2 text-xs text-warning-700 dark:border-warning-700/60 dark:bg-warning-900/30 dark:text-warning-300"
        role="status"
      >
        <span>Some chart data didn't load: {{ error.message }}</span>
        <BaseButton variant="ghost" size="sm" @click="refresh">Retry</BaseButton>
      </div>

      <PatientHeaderBand
        :patient="patient"
        :allergy-count="allergies.length"
        :active-problem-count="activeProblemCount"
        @edit="onEditOpen"
        @new-encounter="onNewEncounter"
      />

      <!-- Quick chips -->
      <div class="mb-5 flex flex-wrap items-center gap-2">
        <span
          v-for="chip in chips"
          :key="chip.label"
          class="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs"
        >
          <span class="font-medium text-ink-muted">{{ chip.label }}</span>
          <span class="text-ink">{{ chip.value }}</span>
        </span>
        <span class="ml-auto flex items-center gap-2 text-[11px] text-ink-muted">
          <BaseBadge variant="info">Mock data</BaseBadge>
          <button
            type="button"
            class="hover:underline"
            @click="refresh"
          >
            Refresh
          </button>
        </span>
      </div>

      <!-- Vitals strip -->
      <section class="mb-6">
        <VitalsStrip :vitals="vitals" :loading="loading" />
      </section>

      <!-- Main grid -->
      <div class="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <div class="space-y-5 lg:col-span-7">
          <ProblemListCard :problems="problems" :loading="loading" />
          <MedicationsCard :medications="medications" :loading="loading" />
          <EncountersCard :encounters="encounters" :loading="loading" />
        </div>
        <div class="space-y-5 lg:col-span-5">
          <AllergiesCard :allergies="allergies" :loading="loading" />
          <LabsCard :labs="labs" :loading="loading" />
        </div>
      </div>
    </template>

    <BaseModal v-model:open="editOpen" title="Edit demographics">
      <form class="grid grid-cols-1 gap-4 sm:grid-cols-2" @submit.prevent="onEditSave">
        <BaseInput
          v-model="editForm.firstName"
          label="First name"
          autocomplete="given-name"
        />
        <BaseInput
          v-model="editForm.lastName"
          label="Last name"
          autocomplete="family-name"
        />
        <BaseInput
          v-model="editForm.phone"
          label="Phone"
          type="tel"
          autocomplete="tel"
        />
        <BaseInput
          v-model="editForm.email"
          label="Email"
          type="email"
          autocomplete="email"
        />
        <p class="sm:col-span-2 text-xs text-ink-muted">
          Demographics edits are local to this preview — a FHIR PATCH back
          to OpenEMR is a post-deadline follow-up.
        </p>
      </form>
      <template #footer>
        <div class="flex items-center justify-end gap-2">
          <transition
            enter-active-class="transition-opacity duration-200"
            leave-active-class="transition-opacity duration-300"
            enter-from-class="opacity-0"
            leave-to-class="opacity-0"
          >
            <span v-if="editSaved" class="text-xs text-success-600" role="status">
              Saved (preview only)
            </span>
          </transition>
          <BaseButton variant="ghost" size="sm" @click="editOpen = false">
            Cancel
          </BaseButton>
          <BaseButton variant="primary" size="sm" @click="onEditSave">
            Save
          </BaseButton>
        </div>
      </template>
    </BaseModal>
  </div>
</template>
