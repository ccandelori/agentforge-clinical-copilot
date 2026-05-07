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
    try {
      const [p, v, pr, m, a, e, l] = await Promise.all([
        getPatient(id),
        getVitals(id),
        getProblems(id),
        getMedications(id),
        getAllergies(id),
        getEncounters(id),
        getLabs(id),
      ])
      patient.value = p
      vitals.value = v
      problems.value = pr
      medications.value = m
      allergies.value = a
      encounters.value = e
      labs.value = l
    } catch (err) {
      error.value = err instanceof Error ? err : new Error(String(err))
    } finally {
      loading.value = false
    }
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
