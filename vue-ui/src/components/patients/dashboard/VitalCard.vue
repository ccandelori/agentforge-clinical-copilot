<script setup lang="ts">
import { computed } from 'vue'

import Sparkline from './Sparkline.vue'

type Tone = 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'

interface Props {
  readonly label: string
  readonly value: string
  readonly unit?: string
  readonly history: readonly number[]
  readonly tone?: Tone
}

const props = withDefaults(defineProps<Props>(), {
  unit: '',
  tone: 'primary',
})

interface Delta {
  readonly text: string
  readonly direction: 'up' | 'down' | 'flat'
}

const delta = computed<Delta | null>(() => {
  if (props.history.length < 2) return null
  const last = props.history[props.history.length - 1]!
  const prev = props.history[props.history.length - 2]!
  const diff = last - prev
  if (Math.abs(diff) < 0.05) return { text: '0', direction: 'flat' }
  const sign = diff > 0 ? '+' : '−'
  return {
    text: `${sign}${Math.abs(diff).toFixed(diff % 1 === 0 ? 0 : 1)}`,
    direction: diff > 0 ? 'up' : 'down',
  }
})

const toneClass = computed<string>(() => {
  switch (props.tone) {
    case 'success':
      return 'text-success-600'
    case 'warning':
      return 'text-warning-600'
    case 'danger':
      return 'text-danger-600'
    case 'info':
      return 'text-info-600'
    case 'neutral':
      return 'text-ink-muted'
    case 'primary':
      return 'text-primary-600'
  }
})

const deltaClass = computed<string>(() => {
  if (!delta.value) return 'text-ink-muted'
  switch (delta.value.direction) {
    case 'up':
      return 'text-warning-600'
    case 'down':
      return 'text-info-600'
    case 'flat':
      return 'text-ink-muted'
  }
})
</script>

<template>
  <div
    class="card flex min-w-[10rem] flex-col gap-2 px-4 py-3 transition-shadow hover:shadow-card-lg"
  >
    <div class="flex items-center justify-between">
      <span class="text-[11px] font-medium uppercase tracking-wide text-ink-muted">
        {{ label }}
      </span>
      <span v-if="delta" class="text-[11px] font-medium tabular-nums" :class="deltaClass">
        <span aria-hidden="true">
          {{ delta.direction === 'up' ? '↑' : delta.direction === 'down' ? '↓' : '·' }}
        </span>
        {{ delta.text }}
      </span>
    </div>
    <div class="flex items-baseline gap-1">
      <span class="text-2xl font-semibold tabular-nums tracking-tight">{{ value }}</span>
      <span v-if="unit" class="text-xs text-ink-muted">{{ unit }}</span>
    </div>
    <div :class="toneClass">
      <Sparkline :values="history" :width="120" :height="28" />
    </div>
  </div>
</template>
