import { computed, type ComputedRef, type Ref } from 'vue'
import { useFhirResource } from '@/composables/useFhirResource'

// Pulls vital-signs Observations for a patient and projects the
// FHIR bundle into per-metric time series. Wraps `useFhirResource`
// rather than reimplementing the fetch — auth (HttpOnly session
// cookie) and error shape stay identical to every other card.
//
// Sparse Synthea data (see memory `project_dashboard_data_gaps`) is
// expected: any series may be empty. Consumers must render gracefully
// with 0 or 1 data point. We never invent values.

export interface VitalPoint {
  readonly value: number
  readonly date: string
}

export type VitalSeries = readonly VitalPoint[]

export interface VitalsHistory {
  readonly hr: VitalSeries
  readonly sysBp: VitalSeries
  readonly diaBp: VitalSeries
  readonly temp: VitalSeries
  readonly spo2: VitalSeries
  readonly weight: VitalSeries
  readonly height: VitalSeries
  readonly bmi: VitalSeries
}

export interface VitalsLatest {
  readonly hr: number | null
  readonly sysBp: number | null
  readonly diaBp: number | null
  readonly temp: number | null
  readonly spo2: number | null
  readonly weight: number | null
  readonly height: number | null
  readonly bmi: number | null
}

export type VitalsStatus = 'idle' | 'loading' | 'success' | 'error'

export interface UseVitals {
  history: ComputedRef<VitalsHistory>
  latest: ComputedRef<VitalsLatest>
  status: Ref<VitalsStatus>
  error: Ref<Error | null>
  refetch: () => Promise<void>
}

// LOINC codes (and synonyms) used by HL7 + Argonaut vital-sign profiles.
// Some servers emit either of two body-temperature codes; same for SpO2
// (oximetry vs. arterial gas) and weight (with vs. without clothing).
const LOINC_HR = ['8867-4'] as const
const LOINC_SYSTOLIC = ['8480-6'] as const
const LOINC_DIASTOLIC = ['8462-4'] as const
const LOINC_TEMP = ['8310-5', '8331-1'] as const
const LOINC_SPO2 = ['2708-6', '59408-5'] as const
const LOINC_WEIGHT = ['29463-7', '3141-9'] as const
const LOINC_HEIGHT = ['8302-2'] as const
const LOINC_BMI = ['39156-5'] as const

function isObservation(
  r: fhir4.Resource | undefined,
): r is fhir4.Observation {
  return (
    r !== undefined && (r as fhir4.Observation).resourceType === 'Observation'
  )
}

function isBundle(b: unknown): b is fhir4.Bundle {
  return (
    b !== null
    && typeof b === 'object'
    && (b as fhir4.Bundle).resourceType === 'Bundle'
  )
}

// Top-level Observation can be a single value (`valueQuantity`) or a
// panel with `component` entries (BP comes through as one Observation
// with two components — systolic + diastolic — under LOINC 85354-9).
// We pluck both shapes here so the panel form Just Works.
function extractValueForCode(
  obs: fhir4.Observation,
  codes: readonly string[],
): number | null {
  const codeSet = new Set<string>(codes)
  // Top-level match.
  for (const c of obs.code?.coding ?? []) {
    if (c.code !== undefined && codeSet.has(c.code)) {
      const v = obs.valueQuantity?.value
      return typeof v === 'number' ? v : null
    }
  }
  // Component match (e.g. BP panel).
  for (const comp of obs.component ?? []) {
    for (const c of comp.code?.coding ?? []) {
      if (c.code !== undefined && codeSet.has(c.code)) {
        const v = comp.valueQuantity?.value
        if (typeof v === 'number') return v
      }
    }
  }
  return null
}

function pickEffectiveDate(obs: fhir4.Observation): string | null {
  const eff = obs.effectiveDateTime
    ?? obs.effectivePeriod?.start
    ?? obs.effectiveInstant
    ?? obs.issued
  return eff ?? null
}

function buildSeries(
  observations: readonly fhir4.Observation[],
  codes: readonly string[],
): VitalPoint[] {
  const out: VitalPoint[] = []
  for (const obs of observations) {
    const value = extractValueForCode(obs, codes)
    if (value === null) continue
    const date = pickEffectiveDate(obs)
    if (date === null) continue
    out.push({ value, date })
  }
  // Ascending: sparkline reads left = oldest, right = newest.
  out.sort((a, b) => a.date.localeCompare(b.date))
  return out
}

function lastValue(series: readonly VitalPoint[]): number | null {
  if (series.length === 0) return null
  return series[series.length - 1]!.value
}

// BMI = kg / (m^2). When the vital panel doesn't carry an explicit
// 39156-5, we synthesize from co-located weight + height pairs that
// share the same effectiveDate. Weight may arrive in kg, lb, etc.;
// since we don't have unit context here, we trust whatever LOINC
// 29463-7 reports — that code is canonically kilograms.
function computeBmiSeries(
  weight: VitalSeries,
  height: VitalSeries,
): VitalPoint[] {
  if (weight.length === 0 || height.length === 0) return []
  const heightByDate = new Map<string, number>()
  for (const h of height) heightByDate.set(h.date, h.value)
  const out: VitalPoint[] = []
  for (const w of weight) {
    const h = heightByDate.get(w.date)
    if (h === undefined || h <= 0) continue
    const meters = h / 100 // LOINC 8302-2 reports cm.
    const bmi = w.value / (meters * meters)
    if (Number.isFinite(bmi)) out.push({ value: bmi, date: w.date })
  }
  out.sort((a, b) => a.date.localeCompare(b.date))
  return out
}

export function useVitals(pid: string): UseVitals {
  const path
    = `/api/fhir/Observation`
    + `?patient=${encodeURIComponent(pid)}`
    + `&category=vital-signs`
    + `&_count=50`
    + `&_sort=-date`
  const { status, data, error, refetch } = useFhirResource<fhir4.Bundle>(path)

  const observations = computed<fhir4.Observation[]>(() => {
    const bundle = data.value
    if (!isBundle(bundle) || !bundle.entry) return []
    const out: fhir4.Observation[] = []
    for (const entry of bundle.entry) {
      const r = entry.resource
      if (isObservation(r)) out.push(r)
    }
    return out
  })

  const history = computed<VitalsHistory>(() => {
    const obs = observations.value
    const weight = buildSeries(obs, LOINC_WEIGHT)
    const height = buildSeries(obs, LOINC_HEIGHT)
    const explicitBmi = buildSeries(obs, LOINC_BMI)
    const bmi = explicitBmi.length > 0
      ? explicitBmi
      : computeBmiSeries(weight, height)
    return {
      hr: buildSeries(obs, LOINC_HR),
      sysBp: buildSeries(obs, LOINC_SYSTOLIC),
      diaBp: buildSeries(obs, LOINC_DIASTOLIC),
      temp: buildSeries(obs, LOINC_TEMP),
      spo2: buildSeries(obs, LOINC_SPO2),
      weight,
      height,
      bmi,
    }
  })

  const latest = computed<VitalsLatest>(() => {
    const h = history.value
    return {
      hr: lastValue(h.hr),
      sysBp: lastValue(h.sysBp),
      diaBp: lastValue(h.diaBp),
      temp: lastValue(h.temp),
      spo2: lastValue(h.spo2),
      weight: lastValue(h.weight),
      height: lastValue(h.height),
      bmi: lastValue(h.bmi),
    }
  })

  return { history, latest, status, error, refetch }
}
