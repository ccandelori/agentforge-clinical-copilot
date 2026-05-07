<script setup lang="ts">
import { computed } from 'vue'

// Hand-rolled SVG sparkline. Polyline + faint area fill + endpoint dot,
// scaled to the observed min/max so flat series collapse cleanly to
// the midline rather than blowing up on a divide-by-zero. Lifted from
// `vue-ui/src/components/patients/dashboard/Sparkline.vue` — the math
// is identical, the styling switches from Tailwind utility classes to
// design-token CSS vars (`var(--accent)`, `var(--ink-muted)`).
//
// With <2 data points (the realistic case for sparse Synthea vitals,
// per memory `project_dashboard_data_gaps`) we render a dashed midline
// rather than drop the whole component — keeps the card layout stable.

interface Props {
  readonly values: readonly number[]
  readonly width?: number
  readonly height?: number
  readonly color?: string
}

const props = withDefaults(defineProps<Props>(), {
  width: 96,
  height: 28,
  color: 'currentColor',
})

interface PathData {
  readonly line: string
  readonly area: string
  readonly lastX: number
  readonly lastY: number
}

const pathData = computed<PathData | null>(() => {
  const values = props.values
  if (values.length < 2) return null

  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const padY = 2
  const usableH = props.height - padY * 2
  const stepX = props.width / (values.length - 1)

  const points: ReadonlyArray<readonly [number, number]> = values.map(
    (v, i): readonly [number, number] => {
      const x = i * stepX
      const y = padY + (1 - (v - min) / range) * usableH
      return [x, y] as const
    },
  )

  const line = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ')

  const first = points[0]!
  const last = points[points.length - 1]!
  const area
    = `${line} L${last[0].toFixed(2)},${props.height} `
    + `L${first[0].toFixed(2)},${props.height} Z`

  return { line, area, lastX: last[0], lastY: last[1] }
})
</script>

<template>
  <svg
    :width="props.width"
    :height="props.height"
    :viewBox="`0 0 ${props.width} ${props.height}`"
    role="img"
    aria-hidden="true"
    class="d-block"
    style="overflow: visible"
  >
    <template v-if="pathData">
      <path
        :d="pathData.area"
        :fill="props.color"
        fill-opacity="0.12"
        stroke="none"
      />
      <path
        :d="pathData.line"
        :stroke="props.color"
        stroke-width="1.5"
        fill="none"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <circle
        :cx="pathData.lastX"
        :cy="pathData.lastY"
        r="2"
        :fill="props.color"
      />
    </template>
    <line
      v-else
      x1="0"
      :y1="props.height / 2"
      :x2="props.width"
      :y2="props.height / 2"
      stroke="currentColor"
      stroke-opacity="0.25"
      stroke-dasharray="2 2"
    />
  </svg>
</template>
