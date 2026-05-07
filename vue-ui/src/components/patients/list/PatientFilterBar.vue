<script setup lang="ts">
import { computed } from 'vue'

import BaseButton from '@/components/ui/BaseButton.vue'

export type GenderFilter = 'all' | 'male' | 'female'
export type AgeBandFilter = 'all' | '0-17' | '18-64' | '65+'
export type StatusFilter = 'all' | 'active' | 'inactive'

export interface PatientFilters {
  readonly gender: GenderFilter
  readonly ageBand: AgeBandFilter
  readonly status: StatusFilter
}

interface Props {
  modelValue: PatientFilters
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: PatientFilters): void
  (e: 'clear'): void
}>()

interface ChipOption<T extends string> {
  readonly value: T
  readonly label: string
}

const GENDER_OPTIONS: ReadonlyArray<ChipOption<GenderFilter>> = [
  { value: 'all', label: 'All' },
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
]

const AGE_OPTIONS: ReadonlyArray<ChipOption<AgeBandFilter>> = [
  { value: 'all', label: 'All ages' },
  { value: '0-17', label: '0–17' },
  { value: '18-64', label: '18–64' },
  { value: '65+', label: '65+' },
]

const STATUS_OPTIONS: ReadonlyArray<ChipOption<StatusFilter>> = [
  { value: 'all', label: 'Any status' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
]

const hasActiveFilters = computed<boolean>(
  () =>
    props.modelValue.gender !== 'all' ||
    props.modelValue.ageBand !== 'all' ||
    props.modelValue.status !== 'all',
)

function setGender(value: GenderFilter): void {
  emit('update:modelValue', { ...props.modelValue, gender: value })
}

function setAge(value: AgeBandFilter): void {
  emit('update:modelValue', { ...props.modelValue, ageBand: value })
}

function setStatus(value: StatusFilter): void {
  emit('update:modelValue', { ...props.modelValue, status: value })
}

function clearAll(): void {
  emit('update:modelValue', { gender: 'all', ageBand: 'all', status: 'all' })
  emit('clear')
}

function chipClass(active: boolean): string {
  return active
    ? 'border-primary-600 bg-primary-600 text-white'
    : 'border-line bg-surface text-ink-muted hover:text-ink hover:bg-surface-2'
}
</script>

<template>
  <div
    class="flex flex-wrap items-center gap-x-4 gap-y-2"
    role="group"
    aria-label="Patient filters"
  >
    <div class="flex items-center gap-1.5">
      <span class="text-xs font-medium uppercase tracking-wide text-ink-muted">
        Gender
      </span>
      <button
        v-for="opt in GENDER_OPTIONS"
        :key="`g-${opt.value}`"
        type="button"
        class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        :class="chipClass(modelValue.gender === opt.value)"
        :aria-pressed="modelValue.gender === opt.value"
        @click="setGender(opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>

    <div class="flex items-center gap-1.5">
      <span class="text-xs font-medium uppercase tracking-wide text-ink-muted">
        Age
      </span>
      <button
        v-for="opt in AGE_OPTIONS"
        :key="`a-${opt.value}`"
        type="button"
        class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        :class="chipClass(modelValue.ageBand === opt.value)"
        :aria-pressed="modelValue.ageBand === opt.value"
        @click="setAge(opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>

    <div class="flex items-center gap-1.5">
      <span class="text-xs font-medium uppercase tracking-wide text-ink-muted">
        Status
      </span>
      <button
        v-for="opt in STATUS_OPTIONS"
        :key="`s-${opt.value}`"
        type="button"
        class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
        :class="chipClass(modelValue.status === opt.value)"
        :aria-pressed="modelValue.status === opt.value"
        @click="setStatus(opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>

    <BaseButton
      v-if="hasActiveFilters"
      variant="ghost"
      size="sm"
      @click="clearAll"
    >
      Clear filters
    </BaseButton>
  </div>
</template>
