<script setup lang="ts">
import { computed } from 'vue'
import VitalCard from '@/components/VitalCard.vue'
import { useVitals } from '@/composables/useVitals'

// Sticky horizontal row of six vital-sign cards, mirroring the vue-ui
// crib (HR / BP / Temp / SpO2 / Weight / BMI). Each card pulls from
// the per-metric series produced by `useVitals`, which fans out the
// FHIR `Observation?category=vital-signs` bundle into one ascending
// time series per LOINC code (with synonyms for temp, SpO2, weight).
//
// Empty / sparse data is the expected state for many Synthea-imported
// patients (memory `project_dashboard_data_gaps`). Each VitalCard
// renders with zero or one history point without breaking layout —
// the sparkline falls back to a dashed midline, the latest value to
// an em-dash, the delta is hidden.

const props = defineProps<{ pid: string }>()

const { history, latest, status, error } = useVitals(props.pid)

interface VitalEntry {
  readonly key: string
  readonly label: string
  readonly unit: string
  readonly history: readonly number[]
  readonly displayValue: string | null
  readonly tone: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  readonly format?: (value: number) => string
}

function bpDisplay(sys: number | null, dia: number | null): string | null {
  if (sys === null && dia === null) return null
  const s = sys === null ? '—' : Math.round(sys).toString()
  const d = dia === null ? '—' : Math.round(dia).toString()
  return `${s}/${d}`
}

const entries = computed<readonly VitalEntry[]>(() => {
  const h = history.value
  const l = latest.value
  return [
    {
      key: 'hr',
      label: 'Heart rate',
      unit: 'bpm',
      history: h.hr.map((p) => p.value),
      displayValue: null,
      tone: 'danger',
    },
    {
      key: 'bp',
      label: 'Blood pressure',
      unit: 'mmHg',
      // Sparkline tracks systolic — the more clinically meaningful
      // trend line of the pair. Latest is rendered as "sys/dia".
      history: h.sysBp.map((p) => p.value),
      displayValue: bpDisplay(l.sysBp, l.diaBp),
      tone: 'primary',
    },
    {
      key: 'temp',
      label: 'Temperature',
      unit: '°C',
      history: h.temp.map((p) => p.value),
      displayValue: null,
      tone: 'warning',
      format: (v) => v.toFixed(1),
    },
    {
      key: 'spo2',
      label: 'SpO2',
      unit: '%',
      history: h.spo2.map((p) => p.value),
      displayValue: null,
      tone: 'info',
    },
    {
      key: 'weight',
      label: 'Weight',
      unit: 'kg',
      history: h.weight.map((p) => p.value),
      displayValue: null,
      tone: 'success',
      format: (v) => v.toFixed(1),
    },
    {
      key: 'bmi',
      label: 'BMI',
      unit: '',
      history: h.bmi.map((p) => p.value),
      displayValue: null,
      tone: 'neutral',
      format: (v) => v.toFixed(1),
    },
  ]
})

const isLoading = computed<boolean>(
  () => status.value === 'idle' || status.value === 'loading',
)
</script>

<template>
  <section
    class="vitals-strip py-3 px-4 border-bottom"
    aria-label="Recent vitals"
  >
    <div v-if="error !== null" class="alert alert-warning small mb-0" role="alert">
      <i class="bi bi-exclamation-triangle me-1" aria-hidden="true"></i>
      Could not load vitals. {{ error.message }}
    </div>
    <div
      v-else-if="isLoading"
      class="row g-2 row-cols-2 row-cols-md-3 row-cols-lg-6"
      aria-busy="true"
    >
      <div v-for="n in 6" :key="n" class="col">
        <div class="card h-100 border placeholder-glow">
          <div class="card-body p-3 d-flex flex-column gap-2">
            <span class="placeholder col-6"></span>
            <span class="placeholder col-8" style="height: 1.5rem"></span>
            <span class="placeholder col-12" style="height: 1.75rem"></span>
          </div>
        </div>
      </div>
    </div>
    <div v-else class="row g-2 row-cols-2 row-cols-md-3 row-cols-lg-6">
      <div v-for="entry in entries" :key="entry.key" class="col">
        <VitalCard
          :label="entry.label"
          :unit="entry.unit"
          :history="entry.history"
          :tone="entry.tone"
          :display-value="entry.displayValue"
          :format="entry.format"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
.vitals-strip {
  background-color: var(--surface-2, var(--bs-tertiary-bg));
  position: sticky;
  /* Sit just below the patient header (sticky-top, no fixed height
     exposed). Fallback offset keeps the strip readable if the header
     scrolls away on narrow viewports. */
  top: var(--header-h, 4rem);
  z-index: 1015;
}
</style>
