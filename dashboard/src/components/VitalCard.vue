<script setup lang="ts">
import { computed } from 'vue'
import Sparkline from '@/components/Sparkline.vue'

// One vital metric: latest reading large, delta arrow + magnitude vs
// the previous reading small, sparkline of recent values beneath.
// Lifted from `vue-ui/src/components/patients/dashboard/VitalCard.vue`,
// retargeted to Bootstrap 5 utility classes + design-token CSS vars
// (`var(--surface)`, `var(--ink)`, `var(--ink-muted)`, `var(--accent)`,
// etc.) instead of Tailwind utilities.
//
// `format` lets the parent decide how to render the latest value
// (e.g. BP wants "120/80", temperature wants 1dp). Defaults to
// integer rounding.

type Tone = 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'

interface Props {
  readonly label: string
  readonly unit?: string
  readonly history: readonly number[]
  readonly format?: (value: number) => string
  readonly tone?: Tone
  // Override for compound values (e.g. BP "120/80") that don't reduce
  // to a single number. When provided, takes precedence over `format`
  // applied to the last history point.
  readonly displayValue?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  unit: '',
  tone: 'primary',
  format: (v: number) => Number(v.toFixed(0)).toString(),
  displayValue: null,
})

interface Delta {
  readonly text: string
  readonly direction: 'up' | 'down' | 'flat'
}

// Latest value as text. Priority: explicit override → formatted last
// history point → em-dash placeholder for empty series.
const latestText = computed<string>(() => {
  if (props.displayValue !== null) return props.displayValue
  if (props.history.length === 0) return '—'
  const v = props.history[props.history.length - 1]!
  return props.format(v)
})

// Delta vs the previous reading. Hidden entirely when we have <2
// points; "0" with a flat indicator when last and prev are within
// rounding noise.
const delta = computed<Delta | null>(() => {
  if (props.history.length < 2) return null
  const last = props.history[props.history.length - 1]!
  const prev = props.history[props.history.length - 2]!
  const diff = last - prev
  if (Math.abs(diff) < 0.05) return { text: '0', direction: 'flat' }
  const sign = diff > 0 ? '+' : '−'
  const magnitude = Math.abs(diff)
  const text = magnitude >= 10
    ? `${sign}${magnitude.toFixed(0)}`
    : `${sign}${magnitude.toFixed(1)}`
  return { text, direction: diff > 0 ? 'up' : 'down' }
})

// Sparkline color picks from the design-token palette. We map each
// tone to a CSS var so dark-mode flip (data-bs-theme) just works.
const sparklineColor = computed<string>(() => {
  switch (props.tone) {
    case 'success':
      return 'var(--bs-success)'
    case 'warning':
      return 'var(--bs-warning)'
    case 'danger':
      return 'var(--bs-danger)'
    case 'info':
      return 'var(--bs-info)'
    case 'neutral':
      return 'var(--ink-muted)'
    case 'primary':
      return 'var(--accent)'
  }
})

const deltaClass = computed<string>(() => {
  const d = delta.value
  if (d === null) return 'text-muted'
  switch (d.direction) {
    case 'up':
      return 'text-warning'
    case 'down':
      return 'text-info'
    case 'flat':
      return 'text-muted'
  }
})

const deltaIcon = computed<string>(() => {
  const d = delta.value
  if (d === null) return ''
  switch (d.direction) {
    case 'up':
      return '↑'
    case 'down':
      return '↓'
    case 'flat':
      return '·'
  }
})
</script>

<template>
  <div class="vital-card card h-100 border">
    <div class="card-body p-3 d-flex flex-column gap-2">
      <div class="d-flex align-items-center justify-content-between">
        <span class="vital-card__label small text-muted text-uppercase fw-medium">
          {{ props.label }}
        </span>
        <span
          v-if="delta"
          class="small fw-medium font-monospace"
          :class="deltaClass"
        >
          <span aria-hidden="true">{{ deltaIcon }}</span>
          {{ delta.text }}
        </span>
      </div>
      <div class="d-flex align-items-baseline gap-1">
        <span class="vital-card__value fw-semibold font-monospace">
          {{ latestText }}
        </span>
        <span v-if="props.unit !== ''" class="small text-muted">
          {{ props.unit }}
        </span>
      </div>
      <div :style="{ color: sparklineColor }">
        <Sparkline :values="props.history" :width="120" :height="28" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.vital-card {
  background-color: var(--surface, var(--bs-body-bg));
  min-width: 10rem;
}

.vital-card__label {
  font-size: 0.6875rem;
  letter-spacing: 0.04em;
}

.vital-card__value {
  font-size: 1.5rem;
  line-height: 1.1;
  color: var(--ink, var(--bs-body-color));
}
</style>
