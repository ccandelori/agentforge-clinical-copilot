/**
 * Mock API module for the Vue UI scaffold.
 *
 * Wave 2 agents call these functions instead of hitting the real OpenEMR
 * FHIR API. All functions return Promises with a small artificial delay
 * to make loading states realistic.
 *
 * Wave 3 will swap this module out for a real FHIR client; the surface
 * area should stay stable.
 */

// ---------------------------------------------------------------------------
// Types
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function delay(): Promise<void> {
  const ms = 150 + Math.floor(Math.random() * 150)
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function id(prefix: string, n: number): string {
  return `${prefix}-${n.toString().padStart(4, '0')}`
}

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

const PATIENTS: readonly Patient[] = [
  {
    id: 'p-0001',
    mrn: 'MRN-1001',
    firstName: 'Alvera',
    lastName: 'Beahan',
    dob: '1956-03-12',
    sex: 'female',
    phone: '555-0101',
    email: 'alvera.beahan@example.com',
    address: { line1: '14 Maple St', city: 'Bedford', state: 'MA', postal: '01730' },
    pcp: 'Dr. Patel',
    insurance: 'Medicare',
  },
  {
    id: 'p-0002',
    mrn: 'MRN-1002',
    firstName: 'Hassan',
    lastName: 'Ondricka',
    dob: '1978-09-04',
    sex: 'male',
    phone: '555-0102',
    email: 'hassan.ondricka@example.com',
    address: { line1: '88 Cedar Ave', city: 'Cambridge', state: 'MA', postal: '02139' },
    pcp: 'Dr. Lee',
    insurance: 'BCBS',
  },
  {
    id: 'p-0003',
    mrn: 'MRN-1003',
    firstName: 'Marisol',
    lastName: 'Reichel',
    dob: '1991-12-21',
    sex: 'female',
    phone: '555-0103',
    email: 'marisol.reichel@example.com',
    address: { line1: '230 Oak Rd', city: 'Somerville', state: 'MA', postal: '02143' },
    pcp: 'Dr. Patel',
    insurance: 'Aetna',
  },
  {
    id: 'p-0004',
    mrn: 'MRN-1004',
    firstName: 'Tomas',
    lastName: 'Schamberger',
    dob: '1949-06-30',
    sex: 'male',
    phone: '555-0104',
    email: 'tomas.schamberger@example.com',
    address: { line1: '5 Birch Ln', city: 'Lexington', state: 'MA', postal: '02420' },
    pcp: 'Dr. Wong',
    insurance: 'Medicare',
  },
  {
    id: 'p-0005',
    mrn: 'MRN-1005',
    firstName: 'Janelle',
    lastName: 'Kovacek',
    dob: '1985-02-17',
    sex: 'female',
    phone: '555-0105',
    email: 'janelle.kovacek@example.com',
    address: { line1: '1 Pine Ct', city: 'Arlington', state: 'MA', postal: '02474' },
    pcp: 'Dr. Lee',
    insurance: 'United',
  },
  {
    id: 'p-0006',
    mrn: 'MRN-1006',
    firstName: 'Devonte',
    lastName: 'Funk',
    dob: '2002-08-10',
    sex: 'male',
    phone: '555-0106',
    email: 'devonte.funk@example.com',
    address: { line1: '402 Elm St', city: 'Medford', state: 'MA', postal: '02155' },
    pcp: 'Dr. Patel',
    insurance: 'BCBS',
  },
  {
    id: 'p-0007',
    mrn: 'MRN-1007',
    firstName: 'Idella',
    lastName: 'Kuvalis',
    dob: '1962-11-02',
    sex: 'female',
    phone: '555-0107',
    email: 'idella.kuvalis@example.com',
    address: { line1: '77 Walnut Way', city: 'Newton', state: 'MA', postal: '02458' },
    pcp: 'Dr. Wong',
    insurance: 'Aetna',
  },
  {
    id: 'p-0008',
    mrn: 'MRN-1008',
    firstName: 'Maximo',
    lastName: 'Donnelly',
    dob: '1970-04-19',
    sex: 'male',
    phone: '555-0108',
    email: 'maximo.donnelly@example.com',
    address: { line1: '60 Spruce Dr', city: 'Belmont', state: 'MA', postal: '02478' },
    pcp: 'Dr. Lee',
    insurance: 'United',
  },
  {
    id: 'p-0009',
    mrn: 'MRN-1009',
    firstName: 'Luella',
    lastName: 'Hessel',
    dob: '1934-07-25',
    sex: 'female',
    phone: '555-0109',
    email: 'luella.hessel@example.com',
    address: { line1: '12 Hickory St', city: 'Watertown', state: 'MA', postal: '02472' },
    pcp: 'Dr. Patel',
    insurance: 'Medicare',
  },
  {
    id: 'p-0010',
    mrn: 'MRN-1010',
    firstName: 'Coleman',
    lastName: 'Bechtelar',
    dob: '1996-10-08',
    sex: 'male',
    phone: '555-0110',
    email: 'coleman.bechtelar@example.com',
    address: { line1: '301 Sycamore Pl', city: 'Brookline', state: 'MA', postal: '02445' },
    pcp: 'Dr. Wong',
    insurance: 'Aetna',
  },
  {
    id: 'p-0011',
    mrn: 'MRN-1011',
    firstName: 'Stephania',
    lastName: 'Wuckert',
    dob: '1981-05-13',
    sex: 'female',
    phone: '555-0111',
    email: 'stephania.wuckert@example.com',
    address: { line1: '9 Aspen Ct', city: 'Quincy', state: 'MA', postal: '02169' },
    pcp: 'Dr. Lee',
    insurance: 'BCBS',
  },
  {
    id: 'p-0012',
    mrn: 'MRN-1012',
    firstName: 'Buster',
    lastName: 'Larkin',
    dob: '2014-01-29',
    sex: 'male',
    phone: '555-0112',
    email: 'buster.larkin@example.com',
    address: { line1: '44 Cypress St', city: 'Malden', state: 'MA', postal: '02148' },
    pcp: 'Dr. Patel',
    insurance: 'MassHealth',
  },
]

const PROBLEM_POOL: ReadonlyArray<readonly [string, string, ProblemStatus]> = [
  ['I10', 'Essential hypertension', 'active'],
  ['E11.9', 'Type 2 diabetes mellitus without complications', 'active'],
  ['E78.5', 'Hyperlipidemia, unspecified', 'active'],
  ['J45.909', 'Asthma, unspecified', 'active'],
  ['F41.1', 'Generalized anxiety disorder', 'active'],
  ['M54.5', 'Low back pain', 'resolved'],
  ['K21.9', 'Gastroesophageal reflux disease', 'active'],
  ['G47.00', 'Insomnia, unspecified', 'inactive'],
  ['N39.0', 'Urinary tract infection', 'resolved'],
  ['J06.9', 'Acute upper respiratory infection', 'resolved'],
]

const MED_POOL: ReadonlyArray<readonly [string, string, string, string]> = [
  ['Lisinopril', '10 mg', 'oral', 'once daily'],
  ['Metformin', '500 mg', 'oral', 'twice daily'],
  ['Atorvastatin', '20 mg', 'oral', 'once daily at bedtime'],
  ['Albuterol HFA', '90 mcg', 'inhaled', 'as needed'],
  ['Sertraline', '50 mg', 'oral', 'once daily'],
  ['Omeprazole', '20 mg', 'oral', 'once daily before breakfast'],
  ['Ibuprofen', '400 mg', 'oral', 'every 6 hours as needed'],
  ['Amoxicillin', '500 mg', 'oral', 'three times daily x 7 days'],
]

const ALLERGY_POOL: ReadonlyArray<readonly [string, string, AllergySeverity]> = [
  ['Penicillin', 'Hives', 'moderate'],
  ['Peanuts', 'Anaphylaxis', 'severe'],
  ['Sulfa drugs', 'Rash', 'mild'],
  ['Latex', 'Contact dermatitis', 'mild'],
  ['Shellfish', 'Swelling', 'moderate'],
]

const LAB_POOL: ReadonlyArray<readonly [string, string, string, string, LabFlag]> = [
  ['HbA1c', '6.8', '%', '4.0-5.6', 'high'],
  ['Glucose', '102', 'mg/dL', '70-99', 'high'],
  ['LDL', '128', 'mg/dL', '<100', 'high'],
  ['HDL', '52', 'mg/dL', '>40', 'normal'],
  ['Triglycerides', '155', 'mg/dL', '<150', 'high'],
  ['Sodium', '140', 'mmol/L', '136-145', 'normal'],
  ['Potassium', '4.1', 'mmol/L', '3.5-5.0', 'normal'],
  ['Creatinine', '0.9', 'mg/dL', '0.6-1.2', 'normal'],
  ['TSH', '2.4', 'mIU/L', '0.4-4.0', 'normal'],
  ['Hemoglobin', '13.5', 'g/dL', '12.0-15.5', 'normal'],
]

// Pseudo-random but deterministic per-patient generator.
function pickN<T>(arr: ReadonlyArray<T>, n: number, seed: number): T[] {
  const out: T[] = []
  for (let i = 0; i < n; i += 1) {
    out.push(arr[(seed + i * 7) % arr.length] as T)
  }
  return out
}

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString()
}

const PROBLEMS: readonly Problem[] = PATIENTS.flatMap((p, idx) => {
  const count = 2 + (idx % 4) // 2..5
  const picks = pickN(PROBLEM_POOL, count, idx + 1)
  return picks.map((tuple, j) => ({
    id: id(`pr-${p.id}`, j + 1),
    patientId: p.id,
    icd10: tuple[0],
    description: tuple[1],
    status: tuple[2],
    onsetDate: isoDaysAgo(180 + j * 30).slice(0, 10),
  }))
})

const MEDICATIONS: readonly Medication[] = PATIENTS.flatMap((p, idx) => {
  const count = 2 + (idx % 3) // 2..4
  const picks = pickN(MED_POOL, count, idx + 2)
  return picks.map((tuple, j) => ({
    id: id(`med-${p.id}`, j + 1),
    patientId: p.id,
    name: tuple[0],
    dose: tuple[1],
    route: tuple[2],
    frequency: tuple[3],
    status: j === 0 ? 'active' : j === 1 ? 'active' : 'completed',
    prescribedDate: isoDaysAgo(60 + j * 20).slice(0, 10),
    prescriber: p.pcp ?? 'Dr. Patel',
  }))
})

const ALLERGIES: readonly Allergy[] = PATIENTS.flatMap((p, idx) => {
  const count = idx % 3 // 0..2
  const picks = pickN(ALLERGY_POOL, count, idx + 3)
  return picks.map((tuple, j) => ({
    id: id(`alg-${p.id}`, j + 1),
    patientId: p.id,
    substance: tuple[0],
    reaction: tuple[1],
    severity: tuple[2],
    notedDate: isoDaysAgo(365 + j * 30).slice(0, 10),
  }))
})

const VITALS: readonly Vital[] = PATIENTS.flatMap((p, idx) => {
  const count = 3 + (idx % 3) // 3..5
  const out: Vital[] = []
  for (let j = 0; j < count; j += 1) {
    out.push({
      id: id(`vit-${p.id}`, j + 1),
      patientId: p.id,
      recordedAt: isoDaysAgo(j * 30 + 5),
      heightCm: 150 + ((idx * 3 + j) % 35),
      weightKg: 55 + ((idx * 5 + j * 2) % 50),
      systolic: 110 + ((idx * 4 + j) % 30),
      diastolic: 70 + ((idx * 2 + j) % 20),
      heartRate: 60 + ((idx + j * 3) % 30),
      tempC: 36.4 + ((j % 5) * 0.1),
      spo2: 95 + ((idx + j) % 5),
      respRate: 14 + ((idx + j) % 6),
    })
  }
  return out
})

const ENCOUNTERS: readonly Encounter[] = PATIENTS.flatMap((p, idx) => {
  const count = 2 + (idx % 2) // 2..3
  const out: Encounter[] = []
  for (let j = 0; j < count; j += 1) {
    out.push({
      id: id(`enc-${p.id}`, j + 1),
      patientId: p.id,
      date: isoDaysAgo(j * 45 + 10),
      type: j === 0 ? 'Office Visit' : j === 1 ? 'Telehealth' : 'Annual Physical',
      providerName: p.pcp ?? 'Dr. Patel',
      reason: j === 0 ? 'Follow-up: hypertension' : 'Routine check',
      status: j === 0 ? 'finished' : j === 1 ? 'finished' : 'scheduled',
      summary:
        j === 0
          ? 'BP well controlled on current regimen. Continue lisinopril.'
          : 'No new concerns.',
    })
  }
  return out
})

const LABS: readonly LabResult[] = PATIENTS.flatMap((p, idx) => {
  const count = 4 + (idx % 3) // 4..6
  const picks = pickN(LAB_POOL, count, idx + 5)
  return picks.map((tuple, j) => ({
    id: id(`lab-${p.id}`, j + 1),
    patientId: p.id,
    name: tuple[0],
    value: tuple[1],
    unit: tuple[2],
    referenceRange: tuple[3],
    flag: tuple[4],
    collectedAt: isoDaysAgo(j * 14 + 3),
    resultedAt: isoDaysAgo(j * 14 + 2),
  }))
})

function buildAppointmentsForDate(dateIso: string): readonly Appointment[] {
  const day = new Date(dateIso)
  return PATIENTS.slice(0, 8).map((p, idx) => {
    const start = new Date(day)
    start.setHours(9 + idx, 0, 0, 0)
    const end = new Date(start.getTime() + 30 * 60 * 1000)
    const statuses: AppointmentStatus[] = [
      'booked',
      'arrived',
      'fulfilled',
      'booked',
      'no-show',
      'booked',
      'cancelled',
      'arrived',
    ]
    return {
      id: id(`appt-${p.id}`, idx + 1),
      patientId: p.id,
      patientName: `${p.firstName} ${p.lastName}`,
      start: start.toISOString(),
      end: end.toISOString(),
      providerName: p.pcp ?? 'Dr. Patel',
      reason: idx % 2 === 0 ? 'Follow-up' : 'New visit',
      status: statuses[idx] ?? 'booked',
    }
  })
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function listPatients(query?: string): Promise<readonly Patient[]> {
  await delay()
  if (!query) return PATIENTS
  const q = query.toLowerCase()
  return PATIENTS.filter((p) => {
    const name = `${p.firstName} ${p.lastName}`.toLowerCase()
    return name.includes(q) || p.mrn.toLowerCase().includes(q)
  })
}

export async function getPatient(id: string): Promise<Patient | null> {
  await delay()
  return PATIENTS.find((p) => p.id === id) ?? null
}

export async function getEncounters(patientId: string): Promise<readonly Encounter[]> {
  await delay()
  return ENCOUNTERS.filter((e) => e.patientId === patientId)
}

export async function getAppointments(date: string): Promise<readonly Appointment[]> {
  await delay()
  return buildAppointmentsForDate(date)
}

export async function getVitals(patientId: string): Promise<readonly Vital[]> {
  await delay()
  return VITALS.filter((v) => v.patientId === patientId)
}

export async function getProblems(patientId: string): Promise<readonly Problem[]> {
  await delay()
  return PROBLEMS.filter((p) => p.patientId === patientId)
}

export async function getMedications(patientId: string): Promise<readonly Medication[]> {
  await delay()
  return MEDICATIONS.filter((m) => m.patientId === patientId)
}

export async function getAllergies(patientId: string): Promise<readonly Allergy[]> {
  await delay()
  return ALLERGIES.filter((a) => a.patientId === patientId)
}

export async function getLabs(patientId: string): Promise<readonly LabResult[]> {
  await delay()
  return LABS.filter((l) => l.patientId === patientId)
}

export interface SearchResults {
  readonly patients: readonly Patient[]
  readonly encounters: readonly Encounter[]
}

export async function searchAll(query: string): Promise<SearchResults> {
  await delay()
  const q = query.trim().toLowerCase()
  if (!q) return { patients: [], encounters: [] }
  const patients = PATIENTS.filter((p) => {
    const name = `${p.firstName} ${p.lastName}`.toLowerCase()
    return name.includes(q) || p.mrn.toLowerCase().includes(q)
  })
  const encounters = ENCOUNTERS.filter((e) =>
    [e.type, e.reason, e.providerName].some((s) => s.toLowerCase().includes(q)),
  )
  return { patients, encounters }
}
