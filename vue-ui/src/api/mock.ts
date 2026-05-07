/**
 * FHIR-backed implementation; type names preserved from initial mock for source
 * compatibility with the seven Wave-2 screens (PatientList, PatientDashboard,
 * CalendarView, EncounterEditor, plus the dashboard chart cards and the calendar
 * store).
 *
 * The exported types and function signatures here are the load-bearing
 * contract for the rest of `vue-ui/src/`. The guts changed from in-memory seed
 * data to live `fetch` calls against the OpenEMR FHIR API (proxied via the
 * sidecar BFF at `/api/fhir/*`); auth rides on the HttpOnly session cookie set
 * by the sidecar's `/auth/callback` handler — there is no Authorization
 * header to set client-side. Pattern is lifted from
 * `dashboard/src/composables/useFhirResource.ts` and the `is{Resource}` type
 * guards in `dashboard/src/components/AllergiesCard.vue` /
 * `MedicationsCard.vue`.
 *
 * No `localStorage` / `sessionStorage` / `IndexedDB` writes anywhere in this
 * file — pure fetch + map.
 */

// ---------------------------------------------------------------------------
// Local FHIR R4 type fallback
// ---------------------------------------------------------------------------

// TODO: replace with @types/fhir after integration npm install. Once
// `@types/fhir@^0.0.42` is on disk, the global `fhir4.*` namespace becomes
// available and this local declaration can be deleted (the type guards below
// will continue to compile against the upstream definitions because their
// shapes match).
declare namespace fhir4 {
  interface CodeableConcept {
    coding?: Coding[]
    text?: string
  }
  interface Coding {
    system?: string
    code?: string
    display?: string
  }
  interface Quantity {
    value?: number
    unit?: string
    system?: string
    code?: string
  }
  interface Period {
    start?: string
    end?: string
  }
  interface Reference {
    reference?: string
    display?: string
  }
  interface Identifier {
    use?: string
    system?: string
    value?: string
    type?: CodeableConcept
  }
  interface HumanName {
    use?: string
    family?: string
    given?: string[]
    prefix?: string[]
    suffix?: string[]
    text?: string
  }
  interface ContactPoint {
    system?: string
    value?: string
    use?: string
  }
  interface Address {
    use?: string
    line?: string[]
    city?: string
    state?: string
    postalCode?: string
    country?: string
  }
  interface Range {
    low?: Quantity
    high?: Quantity
  }
  interface Resource {
    resourceType: string
    id?: string
  }
  interface BundleEntry {
    resource?: Resource
  }
  interface Bundle extends Resource {
    resourceType: 'Bundle'
    type?: string
    total?: number
    entry?: BundleEntry[]
  }
  interface Patient extends Resource {
    resourceType: 'Patient'
    identifier?: Identifier[]
    name?: HumanName[]
    gender?: 'male' | 'female' | 'other' | 'unknown'
    birthDate?: string
    telecom?: ContactPoint[]
    address?: Address[]
    photo?: { url?: string }[]
    generalPractitioner?: Reference[]
  }
  interface Encounter extends Resource {
    resourceType: 'Encounter'
    status?: string
    class?: Coding
    type?: CodeableConcept[]
    subject?: Reference
    participant?: { individual?: Reference }[]
    period?: Period
    reasonCode?: CodeableConcept[]
  }
  interface Appointment extends Resource {
    resourceType: 'Appointment'
    status?: string
    serviceType?: CodeableConcept[]
    reasonCode?: CodeableConcept[]
    description?: string
    start?: string
    end?: string
    participant?: { actor?: Reference; status?: string }[]
  }
  interface Observation extends Resource {
    resourceType: 'Observation'
    status?: string
    category?: CodeableConcept[]
    code?: CodeableConcept
    subject?: Reference
    effectiveDateTime?: string
    issued?: string
    valueQuantity?: Quantity
    valueString?: string
    valueCodeableConcept?: CodeableConcept
    interpretation?: CodeableConcept[]
    referenceRange?: { low?: Quantity; high?: Quantity; text?: string }[]
    component?: {
      code?: CodeableConcept
      valueQuantity?: Quantity
    }[]
  }
  interface Condition extends Resource {
    resourceType: 'Condition'
    clinicalStatus?: CodeableConcept
    verificationStatus?: CodeableConcept
    code?: CodeableConcept
    subject?: Reference
    onsetDateTime?: string
    onsetPeriod?: Period
    recordedDate?: string
  }
  interface Dosage {
    text?: string
    doseAndRate?: {
      doseQuantity?: Quantity
      doseRange?: Range
    }[]
    route?: CodeableConcept
    timing?: { code?: CodeableConcept }
  }
  interface MedicationRequest extends Resource {
    resourceType: 'MedicationRequest'
    status?: string
    intent?: string
    medicationCodeableConcept?: CodeableConcept
    medicationReference?: Reference
    subject?: Reference
    authoredOn?: string
    requester?: Reference
    dosageInstruction?: Dosage[]
  }
  interface MedicationStatement extends Resource {
    resourceType: 'MedicationStatement'
    status?: string
    medicationCodeableConcept?: CodeableConcept
    medicationReference?: Reference
    subject?: Reference
    effectiveDateTime?: string
    effectivePeriod?: Period
    dateAsserted?: string
    dosage?: Dosage[]
    informationSource?: Reference
  }
  interface AllergyIntolerance extends Resource {
    resourceType: 'AllergyIntolerance'
    clinicalStatus?: CodeableConcept
    verificationStatus?: CodeableConcept
    type?: 'allergy' | 'intolerance'
    category?: ('food' | 'medication' | 'environment' | 'biologic')[]
    criticality?: 'low' | 'high' | 'unable-to-assess'
    code?: CodeableConcept
    patient?: Reference
    recordedDate?: string
    reaction?: {
      manifestation?: CodeableConcept[]
      severity?: 'mild' | 'moderate' | 'severe'
      description?: string
    }[]
  }
}

// ---------------------------------------------------------------------------
// Public types — view-facing contract (preserved verbatim from original mock)
// ---------------------------------------------------------------------------

export type Sex = 'male' | 'female' | 'other' | 'unknown'

export interface Patient {
  readonly id: string
  readonly mrn: string
  readonly firstName: string
  readonly lastName: string
  readonly dob: string // YYYY-MM-DD
  readonly sex: Sex
  readonly phone: string
  readonly email: string
  readonly address: {
    readonly line1: string
    readonly city: string
    readonly state: string
    readonly postal: string
  }
  readonly pcp?: string
  readonly insurance?: string
  readonly photoUrl?: string
}

export type ProblemStatus = 'active' | 'resolved' | 'inactive'

export interface Problem {
  readonly id: string
  readonly patientId: string
  readonly icd10: string
  readonly description: string
  readonly status: ProblemStatus
  readonly onsetDate: string
}

export type MedicationStatus = 'active' | 'completed' | 'stopped'

export interface Medication {
  readonly id: string
  readonly patientId: string
  readonly name: string
  readonly dose: string
  readonly route: string
  readonly frequency: string
  readonly status: MedicationStatus
  readonly prescribedDate: string
  readonly prescriber: string
}

export type AllergySeverity = 'mild' | 'moderate' | 'severe'

export interface Allergy {
  readonly id: string
  readonly patientId: string
  readonly substance: string
  readonly reaction: string
  readonly severity: AllergySeverity
  readonly notedDate: string
}

export interface Vital {
  readonly id: string
  readonly patientId: string
  readonly recordedAt: string // ISO timestamp
  readonly heightCm?: number
  readonly weightKg?: number
  readonly systolic?: number
  readonly diastolic?: number
  readonly heartRate?: number
  readonly tempC?: number
  readonly spo2?: number
  readonly respRate?: number
}

export type EncounterStatus = 'scheduled' | 'in-progress' | 'finished' | 'cancelled'

export interface Encounter {
  readonly id: string
  readonly patientId: string
  readonly date: string // ISO timestamp
  readonly type: string
  readonly providerName: string
  readonly reason: string
  readonly status: EncounterStatus
  readonly summary?: string
}

export type AppointmentStatus = 'booked' | 'arrived' | 'cancelled' | 'no-show' | 'fulfilled'

export interface Appointment {
  readonly id: string
  readonly patientId: string
  readonly patientName: string
  readonly start: string // ISO timestamp
  readonly end: string // ISO timestamp
  readonly providerName: string
  readonly reason: string
  readonly status: AppointmentStatus
}

export type LabFlag = 'low' | 'normal' | 'high' | 'critical'

export interface LabResult {
  readonly id: string
  readonly patientId: string
  readonly name: string
  readonly value: string
  readonly unit?: string
  readonly referenceRange?: string
  readonly flag?: LabFlag
  readonly collectedAt: string
  readonly resultedAt: string
}

export interface User {
  readonly id: string
  readonly username: string
  readonly fullName: string
  readonly role: 'physician' | 'nurse' | 'admin' | 'staff'
}

export interface SearchResults {
  readonly patients: readonly Patient[]
  readonly encounters: readonly Encounter[]
}

// ---------------------------------------------------------------------------
// FHIR fetch helper
// ---------------------------------------------------------------------------

/** Hard-cap on a single FHIR call so a stuck sidecar can't lock the UI. */
const FHIR_TIMEOUT_MS = 5000

/**
 * Single point of contact with the FHIR API. Returns the parsed JSON body as
 * `T` (caller chooses `fhir4.Bundle`, `fhir4.Patient`, etc.). Throws a
 * user-friendly `Error` on transport failure, timeout, or non-2xx response.
 *
 * Auth: the sidecar's HttpOnly session cookie travels on `same-origin`
 * credentials. The browser never sees the OAuth2 access token.
 */
async function fhirFetch<T>(path: string): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), FHIR_TIMEOUT_MS)
  try {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: { Accept: 'application/fhir+json' },
      signal: controller.signal,
    })
    if (!res.ok) {
      throw new Error(`FHIR ${path} returned ${res.status}`)
    }
    return (await res.json()) as T
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === 'AbortError') {
      throw new Error(`FHIR ${path} timed out after ${FHIR_TIMEOUT_MS}ms`)
    }
    if (caught instanceof Error) throw caught
    throw new Error(String(caught))
  } finally {
    clearTimeout(timer)
  }
}

/** Pull entries of a specific resource type out of a Bundle. */
function bundleEntries<T extends fhir4.Resource>(
  bundle: fhir4.Bundle,
  type: T['resourceType'],
): T[] {
  if (!bundle.entry) return []
  const out: T[] = []
  for (const entry of bundle.entry) {
    const r = entry.resource
    if (r === undefined) continue
    if (r.resourceType !== type) continue
    out.push(r as T)
  }
  return out
}

// ---------------------------------------------------------------------------
// Type guards (one per FHIR resource we actually project)
// ---------------------------------------------------------------------------

function isBundle(r: unknown): r is fhir4.Bundle {
  return (
    typeof r === 'object'
    && r !== null
    && (r as fhir4.Resource).resourceType === 'Bundle'
  )
}

function isPatient(r: fhir4.Resource | undefined): r is fhir4.Patient {
  return r !== undefined && r.resourceType === 'Patient'
}

function isObservation(r: fhir4.Resource | undefined): r is fhir4.Observation {
  return r !== undefined && r.resourceType === 'Observation'
}

function isCondition(r: fhir4.Resource | undefined): r is fhir4.Condition {
  return r !== undefined && r.resourceType === 'Condition'
}

function isMedicationRequest(
  r: fhir4.Resource | undefined,
): r is fhir4.MedicationRequest {
  return r !== undefined && r.resourceType === 'MedicationRequest'
}

function isMedicationStatement(
  r: fhir4.Resource | undefined,
): r is fhir4.MedicationStatement {
  return r !== undefined && r.resourceType === 'MedicationStatement'
}

function isAllergyIntolerance(
  r: fhir4.Resource | undefined,
): r is fhir4.AllergyIntolerance {
  return r !== undefined && r.resourceType === 'AllergyIntolerance'
}

function isEncounterResource(r: fhir4.Resource | undefined): r is fhir4.Encounter {
  return r !== undefined && r.resourceType === 'Encounter'
}

function isAppointmentResource(r: fhir4.Resource | undefined): r is fhir4.Appointment {
  return r !== undefined && r.resourceType === 'Appointment'
}

// ---------------------------------------------------------------------------
// FHIR projection helpers
// ---------------------------------------------------------------------------

/** Pick the first telecom value of a given system, or '' if none. */
function pickTelecom(
  telecom: readonly fhir4.ContactPoint[] | undefined,
  system: string,
): string {
  if (!telecom) return ''
  for (const t of telecom) {
    if (t.system === system && t.value !== undefined && t.value !== '') {
      return t.value
    }
  }
  return ''
}

function pickFirstName(p: fhir4.Patient): string {
  return p.name?.[0]?.given?.[0] ?? ''
}

function pickLastName(p: fhir4.Patient): string {
  return p.name?.[0]?.family ?? ''
}

function pickMrn(p: fhir4.Patient): string {
  // Prefer an identifier coded as MR; fall back to the first identifier value.
  const ids = p.identifier ?? []
  for (const i of ids) {
    const code = i.type?.coding?.[0]?.code
    if (code === 'MR' && i.value !== undefined) return i.value
  }
  return ids[0]?.value ?? ''
}

function pickAddress(p: fhir4.Patient): Patient['address'] {
  const a = p.address?.[0]
  return {
    line1: a?.line?.[0] ?? '',
    city: a?.city ?? '',
    state: a?.state ?? '',
    postal: a?.postalCode ?? '',
  }
}

function pickSex(g: fhir4.Patient['gender']): Sex {
  switch (g) {
    case 'male':
    case 'female':
    case 'other':
      return g
    default:
      return 'unknown'
  }
}

function projectPatient(p: fhir4.Patient): Patient {
  const pcp = p.generalPractitioner?.[0]?.display
  const photoUrl = p.photo?.[0]?.url
  return {
    id: p.id ?? '',
    mrn: pickMrn(p),
    firstName: pickFirstName(p),
    lastName: pickLastName(p),
    dob: p.birthDate ?? '',
    sex: pickSex(p.gender),
    phone: pickTelecom(p.telecom, 'phone'),
    email: pickTelecom(p.telecom, 'email'),
    address: pickAddress(p),
    ...(pcp !== undefined ? { pcp } : {}),
    ...(photoUrl !== undefined ? { photoUrl } : {}),
  }
}

function pickCodeableText(c: fhir4.CodeableConcept | undefined): string {
  if (c === undefined) return ''
  if (c.text !== undefined && c.text !== '') return c.text
  return c.coding?.[0]?.display ?? ''
}

function pickIcd10(c: fhir4.CodeableConcept | undefined): string {
  if (!c?.coding) return ''
  for (const coding of c.coding) {
    if (coding.system?.includes('icd-10') && coding.code !== undefined) {
      return coding.code
    }
  }
  return c.coding[0]?.code ?? ''
}

function projectProblem(c: fhir4.Condition): Problem {
  const clinicalCode = c.clinicalStatus?.coding?.[0]?.code
  const status: ProblemStatus =
    clinicalCode === 'active'
      ? 'active'
      : clinicalCode === 'resolved'
        ? 'resolved'
        : 'inactive'
  const onset = c.onsetDateTime ?? c.onsetPeriod?.start ?? c.recordedDate ?? ''
  return {
    id: c.id ?? '',
    patientId: extractRefId(c.subject) ?? '',
    icd10: pickIcd10(c.code),
    description: pickCodeableText(c.code),
    status,
    onsetDate: onset.slice(0, 10),
  }
}

/** Reference shapes are `Patient/{id}` or absolute URLs; pull the trailing id. */
function extractRefId(ref: fhir4.Reference | undefined): string | null {
  if (ref?.reference === undefined) return null
  const slash = ref.reference.lastIndexOf('/')
  return slash === -1 ? ref.reference : ref.reference.slice(slash + 1)
}

function pickMedicationName(
  m: fhir4.MedicationRequest | fhir4.MedicationStatement,
): string {
  const cc = m.medicationCodeableConcept
  if (cc !== undefined) {
    if (cc.text !== undefined && cc.text !== '') return cc.text
    const display = cc.coding?.[0]?.display
    if (display !== undefined && display !== '') return display
  }
  const ref = m.medicationReference
  if (ref?.display !== undefined && ref.display !== '') return ref.display
  return ''
}

function pickDoseString(dosage: fhir4.Dosage | undefined): string {
  if (!dosage) return ''
  // FHIR's doseQuantity is the canonical structured dose; doseRange is a
  // fallback. Prefer "X unit" rendering; if nothing structured is available,
  // fall back to free-text dosage instructions.
  const dr = dosage.doseAndRate?.[0]
  const dq = dr?.doseQuantity
  if (dq?.value !== undefined) {
    const unit = dq.unit ?? dq.code ?? ''
    return unit !== '' ? `${dq.value} ${unit}` : `${dq.value}`
  }
  const range = dr?.doseRange
  if (range?.low?.value !== undefined && range.high?.value !== undefined) {
    const unit = range.low.unit ?? range.high.unit ?? ''
    return unit !== ''
      ? `${range.low.value}-${range.high.value} ${unit}`
      : `${range.low.value}-${range.high.value}`
  }
  return ''
}

function projectMedicationRequest(m: fhir4.MedicationRequest): Medication {
  const dose = pickDoseString(m.dosageInstruction?.[0])
  const route = pickCodeableText(m.dosageInstruction?.[0]?.route)
  const frequency =
    pickCodeableText(m.dosageInstruction?.[0]?.timing?.code)
    || (m.dosageInstruction?.[0]?.text ?? '')
  const status: MedicationStatus =
    m.status === 'active'
      ? 'active'
      : m.status === 'stopped' || m.status === 'cancelled' || m.status === 'on-hold'
        ? 'stopped'
        : 'completed'
  return {
    id: m.id ?? '',
    patientId: extractRefId(m.subject) ?? '',
    name: pickMedicationName(m),
    dose,
    route,
    frequency,
    status,
    prescribedDate: (m.authoredOn ?? '').slice(0, 10),
    prescriber: m.requester?.display ?? '',
  }
}

function projectMedicationStatement(m: fhir4.MedicationStatement): Medication {
  const dose = pickDoseString(m.dosage?.[0])
  const route = pickCodeableText(m.dosage?.[0]?.route)
  const frequency =
    pickCodeableText(m.dosage?.[0]?.timing?.code) || (m.dosage?.[0]?.text ?? '')
  const status: MedicationStatus =
    m.status === 'active'
      ? 'active'
      : m.status === 'stopped' || m.status === 'on-hold' || m.status === 'entered-in-error'
        ? 'stopped'
        : 'completed'
  const started =
    m.effectiveDateTime ?? m.effectivePeriod?.start ?? m.dateAsserted ?? ''
  return {
    id: m.id ?? '',
    patientId: extractRefId(m.subject) ?? '',
    name: pickMedicationName(m),
    dose,
    route,
    frequency,
    status,
    prescribedDate: started.slice(0, 10),
    prescriber: m.informationSource?.display ?? '',
  }
}

function pickAllergyReaction(a: fhir4.AllergyIntolerance): string {
  const r = a.reaction?.[0]
  if (!r) return ''
  return pickCodeableText(r.manifestation?.[0]) || (r.description ?? '')
}

function pickAllergySeverity(a: fhir4.AllergyIntolerance): AllergySeverity {
  // Prefer per-reaction severity; fall back to mapping criticality
  // (high → severe, low → mild, unable-to-assess → moderate).
  const sev = a.reaction?.[0]?.severity
  if (sev === 'mild' || sev === 'moderate' || sev === 'severe') return sev
  switch (a.criticality) {
    case 'high':
      return 'severe'
    case 'low':
      return 'mild'
    default:
      return 'moderate'
  }
}

function projectAllergy(a: fhir4.AllergyIntolerance): Allergy {
  return {
    id: a.id ?? '',
    patientId: extractRefId(a.patient) ?? '',
    substance: pickCodeableText(a.code),
    reaction: pickAllergyReaction(a),
    severity: pickAllergySeverity(a),
    notedDate: (a.recordedDate ?? '').slice(0, 10),
  }
}

function projectEncounter(e: fhir4.Encounter): Encounter {
  const status: EncounterStatus =
    e.status === 'in-progress'
      ? 'in-progress'
      : e.status === 'finished'
        ? 'finished'
        : e.status === 'cancelled'
          ? 'cancelled'
          : 'scheduled'
  return {
    id: e.id ?? '',
    patientId: extractRefId(e.subject) ?? '',
    date: e.period?.start ?? '',
    type: pickCodeableText(e.type?.[0]),
    providerName: e.participant?.[0]?.individual?.display ?? '',
    reason: pickCodeableText(e.reasonCode?.[0]),
    status,
  }
}

function projectAppointment(a: fhir4.Appointment): Appointment {
  // Pull patient from the participant whose actor reference points at Patient/.
  let patientId = ''
  let patientName = ''
  for (const p of a.participant ?? []) {
    const ref = p.actor?.reference ?? ''
    if (ref.startsWith('Patient/')) {
      patientId = ref.slice('Patient/'.length)
      patientName = p.actor?.display ?? ''
      break
    }
  }
  let providerName = ''
  for (const p of a.participant ?? []) {
    const ref = p.actor?.reference ?? ''
    if (ref.startsWith('Practitioner/')) {
      providerName = p.actor?.display ?? ''
      break
    }
  }
  const status: AppointmentStatus =
    a.status === 'booked'
      ? 'booked'
      : a.status === 'arrived' || a.status === 'checked-in'
        ? 'arrived'
        : a.status === 'cancelled'
          ? 'cancelled'
          : a.status === 'noshow'
            ? 'no-show'
            : a.status === 'fulfilled'
              ? 'fulfilled'
              : 'booked'
  return {
    id: a.id ?? '',
    patientId,
    patientName,
    start: a.start ?? '',
    end: a.end ?? '',
    providerName,
    reason:
      a.description
      ?? pickCodeableText(a.reasonCode?.[0])
      ?? pickCodeableText(a.serviceType?.[0]),
    status,
  }
}

// ---- Vitals ---------------------------------------------------------------

/** LOINC codes we recognise on `Observation`. */
const LOINC = {
  HR: '8867-4',
  SYS_BP: '8480-6',
  DIA_BP: '8462-4',
  TEMP_C: '8310-5',
  TEMP_BODY: '8331-1',
  SPO2_A: '2708-6',
  SPO2_B: '59408-5',
  WEIGHT_KG: '29463-7',
  WEIGHT_LB: '3141-9',
  HEIGHT_CM: '8302-2',
  RESP_RATE: '9279-1',
  BP_PANEL: '85354-9',
} as const

function loincOf(o: fhir4.Observation): string | undefined {
  if (!o.code?.coding) return undefined
  for (const c of o.code.coding) {
    if (c.system === 'http://loinc.org' && c.code !== undefined) return c.code
  }
  return o.code.coding[0]?.code
}

function effectiveOf(o: fhir4.Observation): string {
  return o.effectiveDateTime ?? o.issued ?? ''
}

/**
 * FHIR vitals come one Observation per measurement; the simple `Vital` shape
 * groups everything taken at the same moment. Bucket Observations by their
 * `effectiveDateTime` and merge measurement kinds into a single row.
 */
function projectVitals(observations: readonly fhir4.Observation[]): readonly Vital[] {
  const buckets = new Map<string, Vital>()
  for (const o of observations) {
    const ts = effectiveOf(o)
    if (ts === '') continue
    const code = loincOf(o)
    const existing = buckets.get(ts) ?? {
      id: o.id ?? `vit-${ts}`,
      patientId: extractRefId(o.subject) ?? '',
      recordedAt: ts,
    }
    const next: Mutable<Vital> = { ...existing }

    // BP panel uses components for systolic/diastolic.
    if (code === LOINC.BP_PANEL) {
      for (const comp of o.component ?? []) {
        const compCode = comp.code?.coding?.find((c) => c.system === 'http://loinc.org')?.code
        if (compCode === LOINC.SYS_BP && comp.valueQuantity?.value !== undefined) {
          next.systolic = comp.valueQuantity.value
        }
        if (compCode === LOINC.DIA_BP && comp.valueQuantity?.value !== undefined) {
          next.diastolic = comp.valueQuantity.value
        }
      }
    } else if (code === LOINC.HR && o.valueQuantity?.value !== undefined) {
      next.heartRate = o.valueQuantity.value
    } else if (code === LOINC.SYS_BP && o.valueQuantity?.value !== undefined) {
      next.systolic = o.valueQuantity.value
    } else if (code === LOINC.DIA_BP && o.valueQuantity?.value !== undefined) {
      next.diastolic = o.valueQuantity.value
    } else if (
      (code === LOINC.TEMP_C || code === LOINC.TEMP_BODY)
      && o.valueQuantity?.value !== undefined
    ) {
      next.tempC =
        o.valueQuantity.unit === '[degF]' || o.valueQuantity.code === '[degF]'
          ? ((o.valueQuantity.value - 32) * 5) / 9
          : o.valueQuantity.value
    } else if (
      (code === LOINC.SPO2_A || code === LOINC.SPO2_B)
      && o.valueQuantity?.value !== undefined
    ) {
      next.spo2 = o.valueQuantity.value
    } else if (code === LOINC.WEIGHT_KG && o.valueQuantity?.value !== undefined) {
      next.weightKg = o.valueQuantity.value
    } else if (code === LOINC.WEIGHT_LB && o.valueQuantity?.value !== undefined) {
      next.weightKg = o.valueQuantity.value * 0.45359237
    } else if (code === LOINC.HEIGHT_CM && o.valueQuantity?.value !== undefined) {
      next.heightCm = o.valueQuantity.value
    } else if (code === LOINC.RESP_RATE && o.valueQuantity?.value !== undefined) {
      next.respRate = o.valueQuantity.value
    }

    buckets.set(ts, next)
  }
  // Newest first, mirroring the original mock.
  return Array.from(buckets.values()).sort((a, b) =>
    b.recordedAt.localeCompare(a.recordedAt),
  )
}

/** Helper for in-place mutation while building `Vital` rows. */
type Mutable<T> = { -readonly [P in keyof T]: T[P] }

// ---- Labs -----------------------------------------------------------------

function pickLabFlag(o: fhir4.Observation): LabFlag | undefined {
  const code = o.interpretation?.[0]?.coding?.[0]?.code
  if (code === undefined) return undefined
  // FHIR v3-ObservationInterpretation: H/L/N/A/HH/LL/AA.
  switch (code) {
    case 'H':
    case 'HH':
      return code === 'HH' ? 'critical' : 'high'
    case 'L':
    case 'LL':
      return code === 'LL' ? 'critical' : 'low'
    case 'A':
    case 'AA':
      return 'critical'
    case 'N':
      return 'normal'
    default:
      return undefined
  }
}

function pickLabValue(o: fhir4.Observation): { value: string; unit?: string } {
  if (o.valueQuantity?.value !== undefined) {
    const unit = o.valueQuantity.unit ?? o.valueQuantity.code
    return unit !== undefined ? { value: String(o.valueQuantity.value), unit } : { value: String(o.valueQuantity.value) }
  }
  if (o.valueString !== undefined) return { value: o.valueString }
  if (o.valueCodeableConcept !== undefined) {
    return { value: pickCodeableText(o.valueCodeableConcept) }
  }
  return { value: '' }
}

function pickReferenceRange(o: fhir4.Observation): string | undefined {
  const r = o.referenceRange?.[0]
  if (!r) return undefined
  if (r.text !== undefined && r.text !== '') return r.text
  const lo = r.low?.value
  const hi = r.high?.value
  const unit = r.low?.unit ?? r.high?.unit ?? ''
  if (lo !== undefined && hi !== undefined) {
    return unit !== '' ? `${lo}-${hi} ${unit}` : `${lo}-${hi}`
  }
  if (lo !== undefined) return unit !== '' ? `>${lo} ${unit}` : `>${lo}`
  if (hi !== undefined) return unit !== '' ? `<${hi} ${unit}` : `<${hi}`
  return undefined
}

function projectLab(o: fhir4.Observation): LabResult {
  const { value, unit } = pickLabValue(o)
  const refRange = pickReferenceRange(o)
  const flag = pickLabFlag(o)
  const collected = effectiveOf(o)
  const resulted = o.issued ?? collected
  return {
    id: o.id ?? '',
    patientId: extractRefId(o.subject) ?? '',
    name: pickCodeableText(o.code),
    value,
    ...(unit !== undefined ? { unit } : {}),
    ...(refRange !== undefined ? { referenceRange: refRange } : {}),
    ...(flag !== undefined ? { flag } : {}),
    collectedAt: collected,
    resultedAt: resulted,
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * GET /api/fhir/Patient — list patients, optionally filtered by name.
 * Returns `[]` if the bundle is empty. Throws on transport errors.
 */
export async function listPatients(query?: string): Promise<readonly Patient[]> {
  const params = new URLSearchParams({ _count: '50' })
  if (query !== undefined && query.trim() !== '') {
    params.set('name', query.trim())
  }
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Patient?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.Patient>(bundle, 'Patient').map(projectPatient)
}

/**
 * GET /api/fhir/Patient/{id} — single patient. Returns `null` on 404 to
 * preserve the original mock's signature; throws on other transport errors.
 */
export async function getPatient(id: string): Promise<Patient | null> {
  try {
    const p = await fhirFetch<fhir4.Patient>(
      `/api/fhir/Patient/${encodeURIComponent(id)}`,
    )
    if (!isPatient(p)) return null
    return projectPatient(p)
  } catch (caught) {
    // Distinguish 404 (return null) from other errors (re-throw). The fetch
    // helper formats 404s as "FHIR <path> returned 404"; sniff for that.
    if (caught instanceof Error && /returned 404/.test(caught.message)) {
      return null
    }
    throw caught
  }
}

/**
 * GET /api/fhir/Encounter?patient={id} — patient encounters, newest first.
 */
export async function getEncounters(patientId: string): Promise<readonly Encounter[]> {
  const params = new URLSearchParams({
    patient: patientId,
    _count: '20',
    _sort: '-date',
  })
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Encounter?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.Encounter>(bundle, 'Encounter')
    .filter(isEncounterResource)
    .map(projectEncounter)
}

/**
 * GET /api/fhir/Appointment?date=ge{day}&date=le{nextDay} — Synthea coverage
 * is sparse; an empty bundle is normal and returns `[]`.
 */
export async function getAppointments(date: string): Promise<readonly Appointment[]> {
  // Normalise the input ISO timestamp → YYYY-MM-DD for the FHIR `date`
  // search-modifier window.
  const day = new Date(date)
  if (Number.isNaN(day.getTime())) return []
  const start = day.toISOString().slice(0, 10)
  const next = new Date(day.getTime() + 24 * 60 * 60 * 1000)
    .toISOString()
    .slice(0, 10)
  const params = new URLSearchParams()
  params.append('date', `ge${start}`)
  params.append('date', `le${next}`)
  params.set('_count', '50')
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Appointment?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.Appointment>(bundle, 'Appointment')
    .filter(isAppointmentResource)
    .map(projectAppointment)
}

/**
 * GET /api/fhir/Observation?patient={id}&category=vital-signs — Synthea
 * vitals are typically per-measurement; we bucket by effective time.
 */
export async function getVitals(patientId: string): Promise<readonly Vital[]> {
  const params = new URLSearchParams({
    patient: patientId,
    category: 'vital-signs',
    _count: '50',
    _sort: '-date',
  })
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Observation?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  const obs = bundleEntries<fhir4.Observation>(bundle, 'Observation').filter(
    isObservation,
  )
  return projectVitals(obs)
}

/**
 * GET /api/fhir/Condition?patient={id}&clinical-status=active — patient
 * problem list. Synthea includes encounter-diagnosis Conditions which mostly
 * pass through unchanged; the dashboard card filters by status downstream.
 */
export async function getProblems(patientId: string): Promise<readonly Problem[]> {
  const params = new URLSearchParams({
    patient: patientId,
    'clinical-status': 'active',
    _count: '50',
  })
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Condition?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.Condition>(bundle, 'Condition')
    .filter(isCondition)
    .map(projectProblem)
}

/**
 * GET /api/fhir/MedicationRequest?patient={id} +
 * GET /api/fhir/MedicationStatement?patient={id} — combined medication list.
 * MedicationStatement coverage in OpenEMR is thin (see DEVIATIONS.md on the
 * dashboard-port branch); we tolerate either query failing without losing the
 * other.
 */
export async function getMedications(patientId: string): Promise<readonly Medication[]> {
  const reqParams = new URLSearchParams({ patient: patientId, _count: '50' })
  const stmtParams = new URLSearchParams({ patient: patientId, _count: '50' })
  const [reqBundle, stmtBundle] = await Promise.allSettled([
    fhirFetch<fhir4.Bundle>(`/api/fhir/MedicationRequest?${reqParams.toString()}`),
    fhirFetch<fhir4.Bundle>(`/api/fhir/MedicationStatement?${stmtParams.toString()}`),
  ])
  const out: Medication[] = []
  if (reqBundle.status === 'fulfilled' && isBundle(reqBundle.value)) {
    for (const m of bundleEntries<fhir4.MedicationRequest>(
      reqBundle.value,
      'MedicationRequest',
    )) {
      if (isMedicationRequest(m)) out.push(projectMedicationRequest(m))
    }
  }
  if (stmtBundle.status === 'fulfilled' && isBundle(stmtBundle.value)) {
    for (const m of bundleEntries<fhir4.MedicationStatement>(
      stmtBundle.value,
      'MedicationStatement',
    )) {
      if (isMedicationStatement(m)) out.push(projectMedicationStatement(m))
    }
  }
  // If both queries failed, surface the MedicationRequest error — the
  // primary source.
  if (
    reqBundle.status === 'rejected'
    && stmtBundle.status === 'rejected'
  ) {
    throw reqBundle.reason instanceof Error
      ? reqBundle.reason
      : new Error(String(reqBundle.reason))
  }
  return out
}

/**
 * GET /api/fhir/AllergyIntolerance?patient={id}.
 */
export async function getAllergies(patientId: string): Promise<readonly Allergy[]> {
  const params = new URLSearchParams({ patient: patientId, _count: '50' })
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/AllergyIntolerance?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.AllergyIntolerance>(bundle, 'AllergyIntolerance')
    .filter(isAllergyIntolerance)
    .map(projectAllergy)
}

/**
 * GET /api/fhir/Observation?patient={id}&category=laboratory — patient lab
 * results. Synthea may not populate `interpretation`; the simple `flag` field
 * is therefore optional.
 */
export async function getLabs(patientId: string): Promise<readonly LabResult[]> {
  const params = new URLSearchParams({
    patient: patientId,
    category: 'laboratory',
    _count: '50',
    _sort: '-date',
  })
  const bundle = await fhirFetch<fhir4.Bundle>(
    `/api/fhir/Observation?${params.toString()}`,
  )
  if (!isBundle(bundle)) return []
  return bundleEntries<fhir4.Observation>(bundle, 'Observation')
    .filter(isObservation)
    .map(projectLab)
}

/**
 * Multi-resource search. The Wave-2 surface only consumes `patients`; the
 * `encounters` list is preserved on the return shape but populated lazily —
 * we issue a Patient name search and an Encounter type search in parallel,
 * tolerating either being empty.
 */
export async function searchAll(query: string): Promise<SearchResults> {
  const q = query.trim()
  if (q === '') return { patients: [], encounters: [] }
  const [patients, encounters] = await Promise.all([
    listPatients(q).catch(() => [] as readonly Patient[]),
    fhirFetch<fhir4.Bundle>(
      `/api/fhir/Encounter?type=${encodeURIComponent(q)}&_count=20`,
    )
      .then((b) =>
        isBundle(b)
          ? bundleEntries<fhir4.Encounter>(b, 'Encounter')
              .filter(isEncounterResource)
              .map(projectEncounter)
          : ([] as readonly Encounter[]),
      )
      .catch(() => [] as readonly Encounter[]),
  ])
  return { patients, encounters }
}
