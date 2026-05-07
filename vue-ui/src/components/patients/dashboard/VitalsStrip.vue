<script setup lang="ts">
import { computed } from 'vue'

import type { Vital } from '@/api/mock'

import VitalCard from './VitalCard.vue'

interface Props {
  readonly vitals: readonly Vital[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

// Mock vitals are returned newest-first (j=0 is most recent). Reverse so
// sparklines read left-to-right oldest → newest.
const ordered = computed<readonly Vital[]>(() =>
  [...props.vitals].sort(
    (a, b) => new Date(a.recordedAt).getTime() - new Date(b.recordedAt).getTime(),
  ),
)

type NumericVitalKey =
  | 'heightCm'
  | 'weightKg'
  | 'systolic'
  | 'diastolic'
  | 'heartRate'
  | 'tempC'
  | 'spo2'
  | 'respRate'

function series(key: NumericVitalKey): number[] {
  return ordered.value
    .map((v) => v[key])
    .filter((x): x is number => typeof x === 'number')
}

const heartRate = computed<readonly number[]>(() => series('heartRate'))
const systolic = computed<readonly number[]>(() => series('systolic'))
const diastolic = computed<readonly number[]>(() => series('diastolic'))
const temp = computed<readonly number[]>(() => series('tempC'))
const spo2 = computed<readonly number[]>(() => series('spo2'))
const weight = computed<readonly number[]>(() => series('weightKg'))
const height = computed<readonly number[]>(() => series('heightCm'))

function last<T>(arr: readonly T[]): T | null {
  return arr.length > 0 ? (arr[arr.length - 1] as T) : null
}

const bp = computed<{ value: string; history: readonly number[] }>(() => {
  const s = last(systolic.value)
  const d = last(diastolic.value)
  return {
    value: s !== null && d !== null ? `${s}/${d}` : '—',
    history: systolic.value, // sparkline tracks systolic
  }
})

const bmi = computed<{ value: string; history: readonly number[] }>(() => {
  const series = ordered.value
    .map((v) => {
      if (typeof v.weightKg !== 'number' || typeof v.heightCm !== 'number') return null
      const m = v.heightCm / 100
      return v.weightKg / (m * m)
    })
    .filter((x): x is number => x !== null)
  return {
    value: series.length > 0 ? series[series.length - 1]!.toFixed(1) : '—',
    history: series,
  }
})

interface VitalEntry {
  readonly label: string
  readonly value: string
  readonly unit: string
  readonly history: readonly number[]
  readonly tone: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'
}

const entries = computed<readonly VitalEntry[]>(() => {
  const hr = last(heartRate.value)
  const t = last(temp.value)
  const sp = last(spo2.value)
  const w = last(weight.value)
  const h = last(height.value)
  return [
    {
      label: 'Heart rate',
      value: hr !== null ? String(hr) : '—',
      unit: 'bpm',
      history: heartRate.value,
      tone: 'danger',
    },
    {
      label: 'Blood pressure',
      value: bp.value.value,
      unit: 'mmHg',
      history: bp.value.history,
      tone: 'primary',
    },
    {
      label: 'Temperature',
      value: t !== null ? t.toFixed(1) : '—',
      unit: '°C',
      history: temp.value,
      tone: 'warning',
    },
    {
      label: 'SpO2',
      value: sp !== null ? String(sp) : '—',
      unit: '%',
      history: spo2.value,
      tone: 'info',
    },
    {
      label: 'Weight',
      value: w !== null ? String(w) : '—',
      unit: 'kg',
      history: weight.value,
      tone: 'success',
    },
    {
      label: 'BMI',
      value: bmi.value.value,
      unit: '',
      history: bmi.value.history.length > 0 ? bmi.value.history : (h !== null ? [h] : []),
      tone: 'neutral',
    },
  ]
})
</script>

<template>
  <div v-if="loading" class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
    <div
      v-for="n in 6"
      :key="n"
      class="card flex animate-pulse flex-col gap-2 px-4 py-3"
    >
      <div class="h-3 w-16 rounded bg-surface-2" />
      <div class="h-7 w-20 rounded bg-surface-2" />
      <div class="h-7 w-full rounded bg-surface-2" />
    </div>
  </div>
  <div v-else class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
    <VitalCard
      v-for="entry in entries"
      :key="entry.label"
      :label="entry.label"
      :value="entry.value"
      :unit="entry.unit"
      :history="entry.history"
      :tone="entry.tone"
    />
  </div>
</template>
