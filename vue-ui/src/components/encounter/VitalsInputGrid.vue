<script setup lang="ts">
import { computed } from 'vue'

import type { EncounterVitalsInput } from '@/composables/useEncounterDraft'

interface Props {
  vitals: EncounterVitalsInput
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), { disabled: false })

const emit = defineEmits<{
  (e: 'update', value: EncounterVitalsInput): void
}>()

function update<K extends keyof EncounterVitalsInput>(key: K, value: string): void {
  emit('update', { ...props.vitals, [key]: value })
}

const bmi = computed<string>(() => {
  const w = parseFloat(props.vitals.weightKg)
  const h = parseFloat(props.vitals.heightCm)
  if (!isFinite(w) || !isFinite(h) || h <= 0 || w <= 0) return '—'
  const meters = h / 100
  const v = w / (meters * meters)
  return v.toFixed(1)
})

const bmiCategory = computed<{ label: string; cls: string }>(() => {
  const v = parseFloat(bmi.value)
  if (!isFinite(v)) return { label: '', cls: 'text-ink-muted' }
  if (v < 18.5) return { label: 'Underweight', cls: 'text-info-600' }
  if (v < 25) return { label: 'Normal', cls: 'text-success-600' }
  if (v < 30) return { label: 'Overweight', cls: 'text-warning-600' }
  return { label: 'Obese', cls: 'text-danger-600' }
})

interface FieldDef {
  readonly key: keyof EncounterVitalsInput
  readonly label: string
  readonly unit: string
  readonly placeholder: string
  readonly inputmode: 'decimal' | 'numeric'
}

const fields: readonly FieldDef[] = [
  { key: 'heartRate', label: 'Heart rate', unit: 'bpm', placeholder: '72', inputmode: 'numeric' },
  { key: 'systolic', label: 'BP systolic', unit: 'mmHg', placeholder: '120', inputmode: 'numeric' },
  { key: 'diastolic', label: 'BP diastolic', unit: 'mmHg', placeholder: '80', inputmode: 'numeric' },
  { key: 'tempC', label: 'Temp', unit: '°C', placeholder: '37.0', inputmode: 'decimal' },
  { key: 'respRate', label: 'Resp rate', unit: '/min', placeholder: '16', inputmode: 'numeric' },
  { key: 'spo2', label: 'SpO₂', unit: '%', placeholder: '98', inputmode: 'numeric' },
  { key: 'weightKg', label: 'Weight', unit: 'kg', placeholder: '72.0', inputmode: 'decimal' },
  { key: 'heightCm', label: 'Height', unit: 'cm', placeholder: '170', inputmode: 'numeric' },
]
</script>

<template>
  <div>
    <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <label
        v-for="f in fields"
        :key="f.key"
        class="flex flex-col gap-1 rounded-lg border border-line bg-surface px-3 py-2 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500/30"
      >
        <span class="text-[11px] uppercase tracking-wide text-ink-muted">{{ f.label }}</span>
        <div class="flex items-baseline gap-1">
          <input
            :value="vitals[f.key]"
            :placeholder="f.placeholder"
            :inputmode="f.inputmode"
            :disabled="disabled"
            class="w-full bg-transparent text-base font-medium text-ink focus:outline-none disabled:opacity-50"
            @input="update(f.key, ($event.target as HTMLInputElement).value)"
          />
          <span class="text-xs text-ink-muted">{{ f.unit }}</span>
        </div>
      </label>
      <div class="col-span-2 flex flex-col justify-center rounded-lg border border-dashed border-line bg-surface-2 px-3 py-2 sm:col-span-4">
        <span class="text-[11px] uppercase tracking-wide text-ink-muted">BMI (computed)</span>
        <div class="flex items-baseline gap-2">
          <span class="text-base font-semibold text-ink">{{ bmi }}</span>
          <span class="text-xs" :class="bmiCategory.cls">{{ bmiCategory.label }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
