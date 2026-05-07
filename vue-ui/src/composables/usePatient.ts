import { ref, watch, type Ref } from 'vue'

import {
  getAllergies,
  getEncounters,
  getLabs,
  getMedications,
  getPatient,
  getProblems,
  getVitals,
  type Allergy,
  type Encounter,
  type LabResult,
  type Medication,
  type Patient,
  type Problem,
  type Vital,
} from '@/api/mock'

export interface UsePatientResult {
  readonly patient: Ref<Patient | null>
  readonly vitals: Ref<readonly Vital[]>
  readonly problems: Ref<readonly Problem[]>
  readonly medications: Ref<readonly Medication[]>
  readonly allergies: Ref<readonly Allergy[]>
  readonly encounters: Ref<readonly Encounter[]>
  readonly labs: Ref<readonly LabResult[]>
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
  const loading = ref<boolean>(false)
  const error = ref<Error | null>(null)

  async function load(id: string): Promise<void> {
    loading.value = true
    error.value = null

    // Patient is the only fetch the page truly can't render without —
    // every secondary card consumes the patient's identity / demographics.
    // Try it first; if it fails, surface the error and bail.
    try {
      const p = await getPatient(id)
      if (p === null) {
        error.value = new Error(`Patient ${id} not found`)
        loading.value = false
        return
      }
      patient.value = p
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
    ])
    const [vR, prR, mR, aR, eR, lR] = settled
    vitals.value = vR.status === 'fulfilled' ? vR.value : []
    problems.value = prR.status === 'fulfilled' ? prR.value : []
    medications.value = mR.status === 'fulfilled' ? mR.value : []
    allergies.value = aR.status === 'fulfilled' ? aR.value : []
    encounters.value = eR.status === 'fulfilled' ? eR.value : []
    labs.value = lR.status === 'fulfilled' ? lR.value : []

    // Surface the first failure as a soft warning on the error ref so
    // the page can render a non-blocking notice. Patient + the cards
    // that DID succeed still display.
    const firstFail = settled.find((s) => s.status === 'rejected')
    if (firstFail && firstFail.status === 'rejected') {
      const reason = firstFail.reason
      error.value = reason instanceof Error ? reason : new Error(String(reason))
    }

    loading.value = false
  }

  async function refresh(): Promise<void> {
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
    loading,
    error,
    refresh,
  }
}
