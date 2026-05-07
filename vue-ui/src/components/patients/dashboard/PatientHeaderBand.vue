<script setup lang="ts">
import { computed } from 'vue'

import type { Patient } from '@/api/mock'
import BaseAvatar from '@/components/ui/BaseAvatar.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

interface Props {
  readonly patient: Patient
  readonly allergyCount: number
  readonly activeProblemCount: number
}

const props = defineProps<Props>()
defineEmits<{
  (e: 'edit'): void
  (e: 'newEncounter'): void
}>()

const fullName = computed<string>(
  () => `${props.patient.firstName} ${props.patient.lastName}`,
)

const age = computed<number>(() => {
  const dob = new Date(props.patient.dob)
  const now = new Date()
  let years = now.getFullYear() - dob.getFullYear()
  const m = now.getMonth() - dob.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) years -= 1
  return years
})

const dobFormatted = computed<string>(() =>
  new Date(props.patient.dob).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }),
)

const sexLabel = computed<string>(() => {
  switch (props.patient.sex) {
    case 'male':
      return 'Male'
    case 'female':
      return 'Female'
    case 'other':
      return 'Other'
    case 'unknown':
      return 'Unknown'
  }
})
</script>

<template>
  <div
    class="sticky top-0 z-10 -mx-6 -mt-6 mb-6 border-b border-line bg-surface/95 px-6 py-4 backdrop-blur supports-[backdrop-filter]:bg-surface/80"
  >
    <div class="flex flex-wrap items-center gap-4">
      <BaseAvatar :name="fullName" :src="patient.photoUrl" size="xl" />

      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <h1 class="truncate text-xl font-semibold tracking-tight">
            {{ fullName }}
          </h1>
          <BaseBadge variant="neutral">MRN {{ patient.mrn }}</BaseBadge>
          <BaseBadge v-if="allergyCount > 0" variant="danger">
            <span aria-hidden="true">⚠</span>
            {{ allergyCount }} {{ allergyCount === 1 ? 'allergy' : 'allergies' }}
          </BaseBadge>
          <BaseBadge v-if="activeProblemCount > 0" variant="info">
            {{ activeProblemCount }} active
            {{ activeProblemCount === 1 ? 'problem' : 'problems' }}
          </BaseBadge>
        </div>

        <dl
          class="mt-1.5 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-ink-muted"
        >
          <div class="flex items-center gap-1.5">
            <dt class="sr-only">Date of birth</dt>
            <dd>
              {{ dobFormatted }}
              <span class="text-ink">·</span>
              <span class="text-ink">{{ age }}y</span>
            </dd>
          </div>
          <div class="flex items-center gap-1.5">
            <dt class="sr-only">Sex</dt>
            <dd>{{ sexLabel }}</dd>
          </div>
          <div v-if="patient.phone" class="flex items-center gap-1.5">
            <dt class="sr-only">Phone</dt>
            <dd class="tabular-nums">{{ patient.phone }}</dd>
          </div>
          <div v-if="patient.email" class="hidden items-center gap-1.5 md:flex">
            <dt class="sr-only">Email</dt>
            <dd class="truncate">{{ patient.email }}</dd>
          </div>
        </dl>
      </div>

      <div class="flex shrink-0 items-center gap-2">
        <BaseButton variant="secondary" size="sm" @click="$emit('edit')">
          Edit
        </BaseButton>
        <BaseButton variant="primary" size="sm" @click="$emit('newEncounter')">
          + New encounter
        </BaseButton>
      </div>
    </div>
  </div>
</template>
