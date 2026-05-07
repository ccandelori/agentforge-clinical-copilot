<script setup lang="ts">
import { computed, toRef } from 'vue'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
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
      v-else-if="error"
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

    <!-- Loaded -->
    <template v-else>
      <PatientHeaderBand
        :patient="patient"
        :allergy-count="allergies.length"
        :active-problem-count="activeProblemCount"
        @edit="() => {}"
        @new-encounter="() => {}"
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
  </div>
</template>
