<script setup lang="ts">
import { computed } from 'vue'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import type { Encounter, Patient } from '@/api/mock'

interface Props {
  encounter: Encounter
  patient: Patient
  signedAt: string | null
  canFinalize: boolean
}

const props = defineProps<Props>()

defineEmits<{
  (e: 'sign'): void
  (e: 'save'): void
}>()

const dateLabel = computed<string>(() => {
  const d = new Date(props.encounter.date)
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
})

const dobLabel = computed<string>(() => {
  const dob = new Date(`${props.patient.dob}T00:00:00`)
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  const m = now.getMonth() - dob.getMonth()
  if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) age -= 1
  return `${props.patient.dob} (age ${age})`
})

const statusVariant = computed<'success' | 'warning'>(() =>
  props.signedAt ? 'success' : 'warning',
)
const statusLabel = computed<string>(() => (props.signedAt ? 'Signed' : 'Draft'))
</script>

<template>
  <header
    class="sticky top-0 z-20 -mx-4 mb-6 border-b border-line bg-surface/95 px-4 py-4 backdrop-blur"
  >
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0 flex-1">
        <RouterLink
          :to="{ name: 'patient-dashboard', params: { id: patient.id } }"
          class="mb-2 inline-flex items-center gap-1.5 text-sm font-medium text-ink-muted transition-colors hover:text-primary-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 rounded"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            class="h-4 w-4 shrink-0"
            aria-hidden="true"
          >
            <path d="M19 12H5" />
            <path d="m12 19-7-7 7-7" />
          </svg>
          <span>Back to chart</span>
        </RouterLink>
        <div class="flex flex-wrap items-center gap-2">
          <BaseBadge variant="info">{{ encounter.type }}</BaseBadge>
          <BaseBadge :variant="statusVariant">{{ statusLabel }}</BaseBadge>
          <span class="text-xs text-ink-muted">#{{ encounter.id }}</span>
        </div>
        <div class="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <RouterLink
            :to="{ name: 'patient-dashboard', params: { id: patient.id } }"
            class="text-lg font-semibold text-ink hover:text-primary-700 hover:underline"
          >
            {{ patient.firstName }} {{ patient.lastName }}
          </RouterLink>
          <span class="text-sm text-ink-muted">DOB {{ dobLabel }}</span>
          <span class="text-sm text-ink-muted">MRN {{ patient.mrn }}</span>
        </div>
        <div class="mt-1 text-xs text-ink-muted">
          {{ dateLabel }} · with {{ encounter.providerName }}
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <BaseButton variant="ghost" size="md" :disabled="signedAt !== null" @click="$emit('save')">
          Save draft
        </BaseButton>
        <BaseButton
          variant="primary"
          size="md"
          :disabled="!canFinalize || signedAt !== null"
          @click="$emit('sign')"
        >
          {{ signedAt ? 'Signed' : 'Sign & Finalize' }}
        </BaseButton>
      </div>
    </div>
  </header>
</template>
