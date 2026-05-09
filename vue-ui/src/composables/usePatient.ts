import { ref, watch, type Ref } from 'vue'

import {
  getAllergies,
  getCareTeams,
  getEncounters,
  getLabs,
  getMedications,
  getPatient,
  getProblems,
  getVitals,
  type Allergy,
  type CareTeam,
  type Encounter,
  type LabResult,
  type Medication,
  type Patient,
  type Problem,
  type Vital,
} from '@/api/mock'

// ---------------------------------------------------------------------------
// In-memory patient bundle cache
//
// The patient chart aggregates seven FHIR queries; on the production droplet
// each one is a real round-trip through Apache → sidecar → OpenEMR PHP.
// Re-fetching the whole bundle every time the user navigates back to a
// patient feels slow even when nothing's wrong. A small per-tab cache with
// a short TTL keeps revisits instant while staying honest about staleness.
// ---------------------------------------------------------------------------

interface CachedBundle {
  patient: Patient
  vitals: readonly Vital[]
  problems: readonly Problem[]
  medications: readonly Medication[]
  allergies: readonly Allergy[]
  encounters: readonly Encounter[]
  labs: readonly LabResult[]
  careTeams: readonly CareTeam[]
  fetchedAt: number
}

const CACHE_TTL_MS = 60_000
const cache = new Map<string, CachedBundle>()

/** Drop a patient's cache entry — used by the explicit `refresh()` action. */
function invalidate(id: string): void {
  cache.delete(id)
}

export interface UsePatientResult {
  readonly patient: Ref<Patient | null>
  readonly vitals: Ref<readonly Vital[]>
  readonly problems: Ref<readonly Problem[]>
  readonly medications: Ref<readonly Medication[]>
  readonly allergies: Ref<readonly Allergy[]>
  readonly encounters: Ref<readonly Encounter[]>
  readonly labs: Ref<readonly LabResult[]>
  readonly careTeams: Ref<readonly CareTeam[]>
  readonly loading: Ref<boolean>
  readonly error: Ref<Error | null>
  refresh: () => Promise<void>
}

/**
 * Loads everything that the patient dashboard needs in parallel.
 *
 * Re-fetches whenever `patientId` changes. Errors from any of the
 * underlying calls land on `error` and freeze the existing data so
 * the UI can render an error state without losing context.
 */
export function usePatient(patientId: Ref<string>): UsePatientResult {
  const patient = ref<Patient | null>(null)
  const vitals = ref<readonly Vital[]>([])
  const problems = ref<readonly Problem[]>([])
  const medications = ref<readonly Medication[]>([])
  const allergies = ref<readonly Allergy[]>([])
  const encounters = ref<readonly Encounter[]>([])
  const labs = ref<readonly LabResult[]>([])
  const careTeams = ref<readonly CareTeam[]>([])
  const loading = ref<boolean>(false)
  const error = ref<Error | null>(null)

  function hydrateFromCache(entry: CachedBundle): void {
    patient.value = entry.patient
    vitals.value = entry.vitals
    problems.value = entry.problems
    medications.value = entry.medications
    allergies.value = entry.allergies
    encounters.value = entry.encounters
    labs.value = entry.labs
    careTeams.value = entry.careTeams
    error.value = null
    loading.value = false
  }

  async function load(id: string): Promise<void> {
    // Cache hit within TTL — render instantly from the prior bundle.
    // Encounters drift fastest (signing a note adds a row), but for the
    // length of a single chart-review session a 60s window is generous
    // without feeling stale.
    const cached = cache.get(id)
    if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
      hydrateFromCache(cached)
      return
    }

    loading.value = true
    error.value = null

    // Patient is the only fetch the page truly can't render without —
    // every secondary card consumes the patient's identity / demographics.
    // Try it first; if it fails, surface the error and bail.
    let resolvedPatient: Patient
    try {
      const p = await getPatient(id)
      if (p === null) {
        error.value = new Error(`Patient ${id} not found`)
        loading.value = false
        return
      }
      patient.value = p
      resolvedPatient = p
    } catch (err) {
      error.value = err instanceof Error ? err : new Error(String(err))
      loading.value = false
      return
    }

    // The remaining six fetches are independent — a slow / failing lab
    // query (Synthea Observations are heavy) shouldn't kill problems,
    // meds, encounters, etc. Promise.allSettled lets each card decide
    // whether to render a skeleton or its data, and surfaces the
    // first error on the screen-level error banner without blocking
    // partial rendering.
    const settled = await Promise.allSettled([
      getVitals(id),
      getProblems(id),
      getMedications(id),
      getAllergies(id),
      getEncounters(id),
      getLabs(id),
      getCareTeams(id),
    ])
    const [vR, prR, mR, aR, eR, lR, ctR] = settled
    vitals.value = vR.status === 'fulfilled' ? vR.value : []
    problems.value = prR.status === 'fulfilled' ? prR.value : []
    medications.value = mR.status === 'fulfilled' ? mR.value : []
    allergies.value = aR.status === 'fulfilled' ? aR.value : []
    encounters.value = eR.status === 'fulfilled' ? eR.value : []
    labs.value = lR.status === 'fulfilled' ? lR.value : []
    careTeams.value = ctR.status === 'fulfilled' ? ctR.value : []

    // Surface the first failure as a soft warning on the error ref so
    // the page can render a non-blocking notice. Patient + the cards
    // that DID succeed still display.
    const firstFail = settled.find((s) => s.status === 'rejected')
    if (firstFail && firstFail.status === 'rejected') {
      const reason = firstFail.reason
      error.value = reason instanceof Error ? reason : new Error(String(reason))
    }

    // Only cache when the whole bundle resolved cleanly — caching a
    // partial bundle would freeze user-visible failures into the cache
    // and the soft-warning Retry button would just keep re-rendering
    // the same broken state.
    if (!firstFail) {
      cache.set(id, {
        patient: resolvedPatient,
        vitals: vitals.value,
        problems: problems.value,
        medications: medications.value,
        allergies: allergies.value,
        encounters: encounters.value,
        labs: labs.value,
        careTeams: careTeams.value,
        fetchedAt: Date.now(),
      })
    }

    loading.value = false
  }

  async function refresh(): Promise<void> {
    invalidate(patientId.value)
    await load(patientId.value)
  }

  watch(
    patientId,
    (id) => {
      void load(id)
    },
    { immediate: true },
  )

  return {
    patient,
    vitals,
    problems,
    medications,
    allergies,
    encounters,
    labs,
    careTeams,
    loading,
    error,
    refresh,
  }
}
