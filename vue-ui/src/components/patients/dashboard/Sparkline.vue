<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  readonly values: readonly number[]
  readonly width?: number
  readonly height?: number
  readonly stroke?: string
  readonly fill?: string
}

const props = withDefaults(defineProps<Props>(), {
  width: 96,
  height: 28,
  stroke: 'currentColor',
  fill: 'none',
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

  const points = values.map((v, i) => {
    const x = i * stepX
    const y = padY + (1 - (v - min) / range) * usableH
    return [x, y] as const
  })

  const line = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(' ')

  const first = points[0]!
  const last = points[points.length - 1]!
  const area = `${line} L${last[0].toFixed(2)},${props.height} L${first[0].toFixed(2)},${props.height} Z`

  return { line, area, lastX: last[0], lastY: last[1] }
})
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    aria-hidden="true"
    class="overflow-visible"
  >
    <template v-if="pathData">
      <path
        :d="pathData.area"
        :fill="fill === 'none' ? 'currentColor' : fill"
        fill-opacity="0.1"
        stroke="none"
      />
      <path
        :d="pathData.line"
        :stroke="stroke"
        stroke-width="1.5"
        fill="none"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <circle
        :cx="pathData.lastX"
        :cy="pathData.lastY"
        r="2"
        :fill="stroke"
      />
    </template>
    <line
      v-else
      x1="0"
      :y1="height / 2"
      :x2="width"
      :y2="height / 2"
      stroke="currentColor"
      stroke-opacity="0.2"
      stroke-dasharray="2 2"
    />
  </svg>
</template>
