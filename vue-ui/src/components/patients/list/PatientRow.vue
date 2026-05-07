<script setup lang="ts">
import { computed } from 'vue'

import BaseAvatar from '@/components/ui/BaseAvatar.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { relativeTime } from '@/composables/useRelativeTime'
import type { Patient, Sex } from '@/api/mock'

export type PatientRowField =
  | 'name'
  | 'mrn'
  | 'dob'
  | 'sex'
  | 'lastVisit'
  | 'actions'

interface Props {
  patient: Patient
  field: PatientRowField
  /** Optional last visit ISO timestamp; rendered as relative time. */
  lastVisit?: string | null
  density?: 'comfortable' | 'compact'
}

const props = withDefaults(defineProps<Props>(), {
  lastVisit: null,
  density: 'comfortable',
})

const emit = defineEmits<{
  (e: 'open-actions', patient: Patient): void
}>()

function ageFromDob(dob: string): number {
  const birth = new Date(dob)
  if (Number.isNaN(birth.getTime())) return 0
  const now = new Date()
  let age = now.getUTCFullYear() - birth.getUTCFullYear()
  const m = now.getUTCMonth() - birth.getUTCMonth()
  if (m < 0 || (m === 0 && now.getUTCDate() < birth.getUTCDate())) {
    age -= 1
  }
  return age
}

function badgeVariantForSex(
  sex: Sex,
): 'info' | 'danger' | 'neutral' {
  switch (sex) {
    case 'female':
      return 'danger'
    case 'male':
      return 'info'
    default:
      return 'neutral'
  }
}

const fullName = computed<string>(
  () => `${props.patient.firstName} ${props.patient.lastName}`,
)

const age = computed<number>(() => ageFromDob(props.patient.dob))

const sexLabel = computed<string>(() => {
  const s = props.patient.sex
  return s.charAt(0).toUpperCase() + s.slice(1)
})

const lastVisitLabel = computed<string>(() => {
  if (!props.lastVisit) return 'No visits'
  const text = relativeTime(props.lastVisit)
  return text || 'Unknown'
})

const isCompact = computed<boolean>(() => props.density === 'compact')

function onActionsClick(event: MouseEvent): void {
  event.stopPropagation()
  emit('open-actions', props.patient)
}
</script>

<template>
  <!-- Name cell: avatar + full name + (when comfortable) email subline. -->
  <div v-if="field === 'name'" class="flex items-center gap-3">
    <BaseAvatar
      :name="fullName"
      :src="patient.photoUrl ?? ''"
      :size="isCompact ? 'sm' : 'md'"
    />
    <div class="flex min-w-0 flex-col">
      <span class="truncate font-medium text-ink">{{ fullName }}</span>
      <span v-if="!isCompact" class="truncate text-xs text-ink-muted">
        {{ patient.email }}
      </span>
    </div>
  </div>

  <span v-else-if="field === 'mrn'" class="font-mono text-xs text-ink-muted">
    {{ patient.mrn }}
  </span>

  <div v-else-if="field === 'dob'" class="flex flex-col">
    <span class="text-ink">{{ patient.dob }}</span>
    <span v-if="!isCompact" class="text-xs text-ink-muted">
      Age {{ age }}
    </span>
  </div>

  <BaseBadge v-else-if="field === 'sex'" :variant="badgeVariantForSex(patient.sex)">
    {{ sexLabel }}
  </BaseBadge>

  <span v-else-if="field === 'lastVisit'" class="text-sm text-ink-muted">
    {{ lastVisitLabel }}
  </span>

  <button
    v-else-if="field === 'actions'"
    type="button"
    class="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    aria-haspopup="menu"
    :aria-label="`Actions for ${fullName}`"
    @click="onActionsClick"
  >
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      class="h-4 w-4"
      aria-hidden="true"
    >
      <path
        d="M10 4a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm0 4.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3ZM8.5 14.5a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0Z"
      />
    </svg>
  </button>
</template>
