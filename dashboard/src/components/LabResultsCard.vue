<script setup lang="ts">
import { computed } from 'vue'
import ClinicalCard from '@/components/ClinicalCard.vue'
import { useFhirResource } from '@/composables/useFhirResource'
import { formatFhirDate } from '@/utils/formatDate'

// Lab Results card. One row per unique LOINC code, latest value
// shown with color coding (red = high, blue = low, bold red =
// critical) and an inline SVG sparkline if the same analyte has 3+
// historical measurements. Interpretation codes drive the flag when
// present; numeric refRange comparison is the fallback.

const props = defineProps<{ pid: string }>()

const { status, data, error } = useFhirResource<fhir4.Bundle>(
  `/api/fhir/Observation?patient=${encodeURIComponent(props.pid)}&category=laboratory`,
)

type Flag =
  | 'normal'
  | 'high'
  | 'low'
  | 'critical-high'
  | 'critical-low'
  | 'abnormal'
  | null

interface DataPoint {
  date: string
  value: number
}

interface LabRow {
  key: string
  name: string
  unit: string | null
  latestValue: number | string
  latestEffective: string | null
  refLow: number | null
  refHigh: number | null
  history: DataPoint[]
  flag: Flag
}

function isObservation(
  r: fhir4.Resource | undefined,
): r is fhir4.Observation {
  return (
    r !== undefined && (r as fhir4.Observation).resourceType === 'Observation'
  )
}

function pickCode(o: fhir4.Observation): { code: string; name: string } {
  const code = o.code?.coding?.[0]?.code
  const text = o.code?.text
  const display = o.code?.coding?.[0]?.display
  const name = text ?? display ?? '(unknown analyte)'
  return { code: code !== undefined && code !== '' ? code : name, name }
}

function pickInterpretationCodes(o: fhir4.Observation): string[] {
  const out: string[] = []
  for (const interp of o.interpretation ?? []) {
    for (const c of interp.coding ?? []) {
      if (c.code !== undefined) out.push(c.code)
    }
  }
  return out
}

function pickRefRange(
  o: fhir4.Observation,
): { low: number | null; high: number | null } {
  const range = o.referenceRange?.[0]
  if (!range) return { low: null, high: null }
  return {
    low: range.low?.value ?? null,
    high: range.high?.value ?? null,
  }
}

// Trust explicit interpretation codes first (HL7 v3
// ObservationInterpretation: HH/LL = critical, H/L = out of range,
// A = abnormal, N = normal). Numeric range comparison is the
// fallback for observations that don't carry interpretation.
function deriveFlag(
  interpCodes: string[],
  value: number | null,
  refLow: number | null,
  refHigh: number | null,
): Flag {
  if (interpCodes.includes('HH') || interpCodes.includes('AA')) {
    return 'critical-high'
  }
  if (interpCodes.includes('LL')) return 'critical-low'
  if (interpCodes.includes('H')) return 'high'
  if (interpCodes.includes('L')) return 'low'
  if (interpCodes.includes('A')) return 'abnormal'
  if (interpCodes.includes('N')) return 'normal'
  if (value !== null) {
    if (refHigh !== null && value > refHigh) return 'high'
    if (refLow !== null && value < refLow) return 'low'
    if (refLow !== null || refHigh !== null) return 'normal'
  }
  return null
}

const rows = computed<LabRow[]>(() => {
  const bundle = data.value
  if (!bundle || !bundle.entry) return []

  // Group observations by code so a row is one analyte over time.
  const groups = new Map<string, fhir4.Observation[]>()
  for (const e of bundle.entry) {
    const r = e.resource
    if (!isObservation(r)) continue
    const { code } = pickCode(r)
    const list = groups.get(code) ?? []
    list.push(r)
    groups.set(code, list)
  }

  const out: LabRow[] = []
  for (const [code, observations] of groups.entries()) {
    // Newest first — latest measurement drives the row's display.
    const sorted = [...observations].sort((a, b) => {
      const ad = a.effectiveDateTime ?? ''
      const bd = b.effectiveDateTime ?? ''
      return bd.localeCompare(ad)
    })
    const latest = sorted[0]
    if (!latest) continue
    const { name } = pickCode(latest)
    const valueQ = latest.valueQuantity
    const valueCC = latest.valueCodeableConcept
    const valueS = latest.valueString
    let latestValue: number | string
    let unit: string | null
    if (valueQ?.value !== undefined) {
      latestValue = valueQ.value
      unit = valueQ.unit ?? null
    } else if (valueCC !== undefined) {
      const ccText
        = valueCC.text ?? valueCC.coding?.[0]?.display ?? ''
      if (ccText === '') continue
      latestValue = ccText
      unit = null
    } else if (valueS !== undefined) {
      // Skip OpenEMR's "{entry.value}" template-placeholder bug —
      // the FHIR mapper sometimes emits an unsubstituted template
      // string instead of the actual value (observed on Cause of
      // Death observations).
      if (/^\{[^}]+\}$/.test(valueS)) continue
      latestValue = valueS
      unit = null
    } else {
      // Skip observations without a value.
      continue
    }
    const interp = pickInterpretationCodes(latest)
    const range = pickRefRange(latest)
    const numericValue = typeof latestValue === 'number' ? latestValue : null
    const flag = deriveFlag(interp, numericValue, range.low, range.high)

    // Sparkline history: only quantitative observations, sorted asc.
    const history: DataPoint[] = []
    for (const o of sorted) {
      const v = o.valueQuantity?.value
      const d = o.effectiveDateTime
      if (v !== undefined && d !== undefined) history.push({ date: d, value: v })
    }
    history.sort((a, b) => a.date.localeCompare(b.date))

    out.push({
      key: code,
      name,
      unit,
      latestValue,
      latestEffective: latest.effectiveDateTime ?? null,
      refLow: range.low,
      refHigh: range.high,
      history,
      flag,
    })
  }

  // Newest first across rows.
  out.sort((a, b) => {
    const ad = a.latestEffective ?? ''
    const bd = b.latestEffective ?? ''
    return bd.localeCompare(ad)
  })
  return out
})

const cardState = computed<'loading' | 'empty' | 'error' | 'ready'>(() => {
  if (status.value === 'idle' || status.value === 'loading') return 'loading'
  if (status.value === 'error') return 'error'
  if (rows.value.length === 0) return 'empty'
  return 'ready'
})

function flagClass(flag: Flag): string {
  switch (flag) {
    case 'critical-high':
    case 'critical-low':
      return 'text-danger fw-bold'
    case 'high':
      return 'text-danger'
    case 'low':
      return 'text-primary'
    case 'abnormal':
      return 'text-warning'
    default:
      return ''
  }
}

// SVG polyline points for a 60×16 sparkline. Normalizes value to the
// observed min/max so flat series collapse to a horizontal line at
// mid-height instead of div-by-zero.
function sparklinePoints(history: DataPoint[]): string {
  if (history.length < 3) return ''
  const w = 60
  const h = 16
  const values = history.map((p) => p.value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min
  const dx = w / (history.length - 1)
  const points: string[] = []
  for (let i = 0; i < history.length; i += 1) {
    const x = i * dx
    const y = range === 0 ? h / 2 : h - ((history[i]!.value - min) / range) * h
    points.push(`${x.toFixed(1)},${y.toFixed(1)}`)
  }
  return points.join(' ')
}

function formatValue(v: number | string): string {
  if (typeof v === 'string') return v
  // Trim trailing zeros after rounding to 2dp.
  return Number(v.toFixed(2)).toString()
}
</script>

<template>
  <ClinicalCard
    title="Lab Results"
    :count="cardState === 'ready' ? rows.length : null"
    :state="cardState"
    :error="error"
    collapsible
  >
    <template #loading>
      <div class="placeholder-glow" aria-hidden="true">
        <p class="placeholder col-7 mb-2"></p>
        <p class="placeholder col-5 mb-2"></p>
        <p class="placeholder col-6"></p>
      </div>
    </template>

    <template #empty>
      <div class="text-muted small">No lab results on file.</div>
    </template>

    <ul class="list-unstyled mb-0 d-flex flex-column gap-3">
      <li v-for="row in rows" :key="row.key">
        <div class="d-flex align-items-baseline justify-content-between gap-2">
          <div class="fw-semibold">{{ row.name }}</div>
          <span
            class="d-inline-flex align-items-center gap-2"
            :class="flagClass(row.flag)"
          >
            <svg
              v-if="row.history.length >= 3"
              class="lab-sparkline"
              width="60"
              height="16"
              viewBox="0 0 60 16"
              aria-label="Trend over time"
            >
              <polyline
                :points="sparklinePoints(row.history)"
                stroke="currentColor"
                stroke-width="1.2"
                fill="none"
              />
            </svg>
            <span>
              {{ formatValue(row.latestValue)
              }}<span v-if="row.unit !== null"> {{ row.unit }}</span>
            </span>
          </span>
        </div>
        <div class="small text-muted mt-1 d-flex flex-wrap gap-3">
          <span v-if="row.refLow !== null || row.refHigh !== null">
            Range:
            <template v-if="row.refLow !== null && row.refHigh !== null">
              {{ row.refLow }}–{{ row.refHigh }}
            </template>
            <template v-else-if="row.refLow !== null">
              ≥ {{ row.refLow }}
            </template>
            <template v-else>
              ≤ {{ row.refHigh }}
            </template>
            <span v-if="row.unit !== null"> {{ row.unit }}</span>
          </span>
          <span v-if="row.latestEffective !== null">
            Collected: {{ formatFhirDate(row.latestEffective) }}
          </span>
        </div>
      </li>
    </ul>
  </ClinicalCard>
</template>
