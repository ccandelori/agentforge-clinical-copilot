<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { searchAll, type Patient } from '@/api/mock'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSpinner from '@/components/ui/BaseSpinner.vue'
import { dayKey } from '@/lib/dates'
import {
  APPOINTMENT_TYPES,
  FACILITIES,
  PROVIDERS,
  useCalendarStore,
  type NewAppointmentInput,
} from '@/stores/calendar'

interface Props {
  open: boolean
  /** Pre-filled start time (date + time-of-day). */
  initialStart: Date | null
  initialProvider?: string
}

const props = withDefaults(defineProps<Props>(), {
  initialProvider: '',
})

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'created', input: NewAppointmentInput): void
}>()

const store = useCalendarStore()

const patientQuery = ref<string>('')
const patientResults = ref<readonly Patient[]>([])
const selectedPatient = ref<Patient | null>(null)
const searchLoading = ref<boolean>(false)
const searchOpen = ref<boolean>(false)

const dateValue = ref<string>('') // yyyy-mm-dd
const timeValue = ref<string>('') // HH:mm
const duration = ref<number>(30)
const provider = ref<string>('')
const facility = ref<string>('')
const apptType = ref<string>(APPOINTMENT_TYPES[0] ?? 'Follow-up')
const notes = ref<string>('')

const formError = ref<string>('')
const submitting = ref<boolean>(false)

function pad(n: number): string {
  return n.toString().padStart(2, '0')
}

function formatTimeForInput(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

watch(
  () => [props.open, props.initialStart, props.initialProvider] as const,
  ([open, initial, initialProvider]) => {
    if (!open) return
    const start = initial ?? new Date()
    dateValue.value = dayKey(start)
    timeValue.value = formatTimeForInput(start)
    provider.value = (initialProvider !== '' ? initialProvider : null)
      ?? store.providerFilter.values().next().value
      ?? PROVIDERS[0]
      ?? ''
    facility.value = store.facilityFilter !== '' ? store.facilityFilter : (FACILITIES[0] ?? '')
    apptType.value = APPOINTMENT_TYPES[0] ?? 'Follow-up'
    duration.value = 30
    notes.value = ''
    selectedPatient.value = null
    patientQuery.value = ''
    patientResults.value = []
    searchOpen.value = false
    formError.value = ''
  },
  { immediate: true },
)

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(patientQuery, (q) => {
  if (selectedPatient.value && `${selectedPatient.value.firstName} ${selectedPatient.value.lastName}` === q) {
    return
  }
  selectedPatient.value = null
  if (searchTimer !== null) clearTimeout(searchTimer)
  if (q.trim().length < 2) {
    patientResults.value = []
    searchOpen.value = false
    return
  }
  searchLoading.value = true
  searchOpen.value = true
  searchTimer = setTimeout(() => {
    void runSearch(q)
  }, 200)
})

async function runSearch(q: string): Promise<void> {
  try {
    const res = await searchAll(q)
    patientResults.value = res.patients
  } catch {
    patientResults.value = []
  } finally {
    searchLoading.value = false
  }
}

function pickPatient(p: Patient): void {
  selectedPatient.value = p
  patientQuery.value = `${p.firstName} ${p.lastName}`
  searchOpen.value = false
}

const canSubmit = computed<boolean>(() => {
  return (
    selectedPatient.value !== null
    && dateValue.value !== ''
    && timeValue.value !== ''
    && duration.value > 0
    && provider.value !== ''
  )
})

function submit(): void {
  formError.value = ''
  if (selectedPatient.value === null) {
    formError.value = 'Pick a patient.'
    return
  }
  if (dateValue.value === '' || timeValue.value === '') {
    formError.value = 'Date and time are required.'
    return
  }
  const [y, m, d] = dateValue.value.split('-').map((s) => Number.parseInt(s, 10))
  const [hh, mm] = timeValue.value.split(':').map((s) => Number.parseInt(s, 10))
  if (
    Number.isNaN(y) || Number.isNaN(m) || Number.isNaN(d)
    || Number.isNaN(hh) || Number.isNaN(mm)
  ) {
    formError.value = 'Could not parse date or time.'
    return
  }
  const start = new Date(y, m - 1, d, hh, mm, 0, 0)
  submitting.value = true
  const input: NewAppointmentInput = {
    patientId: selectedPatient.value.id,
    patientName: `${selectedPatient.value.firstName} ${selectedPatient.value.lastName}`,
    providerName: provider.value,
    facilityName: facility.value,
    start,
    durationMinutes: duration.value,
    type: apptType.value,
    notes: notes.value,
  }
  store.addAppointment(input)
  emit('created', input)
  submitting.value = false
  emit('update:open', false)
}

const DURATIONS: readonly number[] = [15, 30, 45, 60]
</script>

<template>
  <BaseModal :open="open" title="New appointment" @update:open="emit('update:open', $event)">
    <form class="flex flex-col gap-4" @submit.prevent="submit">
      <!-- Patient picker -->
      <div class="relative flex flex-col gap-1">
        <label class="text-sm font-medium text-ink" for="patient-search">
          Patient<span class="ml-0.5 text-danger-600">*</span>
        </label>
        <input
          id="patient-search"
          v-model="patientQuery"
          type="text"
          class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          placeholder="Search by name or MRN…"
          autocomplete="off"
          @focus="searchOpen = patientResults.length > 0"
        />
        <div
          v-if="searchOpen"
          class="absolute left-0 right-0 top-full z-30 mt-1 max-h-56 overflow-auto rounded-lg border border-line bg-surface shadow-card-lg"
        >
          <div v-if="searchLoading" class="flex items-center gap-2 px-3 py-2 text-xs text-ink-muted">
            <BaseSpinner size="sm" /> Searching…
          </div>
          <ul v-else-if="patientResults.length > 0" class="py-1">
            <li v-for="p in patientResults" :key="p.id">
              <button
                type="button"
                class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs hover:bg-surface-2"
                @click="pickPatient(p)"
              >
                <span class="font-medium text-ink">{{ p.firstName }} {{ p.lastName }}</span>
                <span class="text-ink-muted">{{ p.mrn }}</span>
              </button>
            </li>
          </ul>
          <div v-else class="px-3 py-2 text-xs text-ink-muted">No patients match.</div>
        </div>
        <p v-if="selectedPatient" class="text-xs text-success-700">
          Selected: {{ selectedPatient.firstName }} {{ selectedPatient.lastName }} · {{ selectedPatient.mrn }}
        </p>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <BaseInput
          v-model="dateValue"
          label="Date"
          type="date"
          required
        />
        <BaseInput
          v-model="timeValue"
          label="Start time"
          type="time"
          required
        />
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-sm font-medium text-ink" for="duration-select">Duration</label>
          <select
            id="duration-select"
            v-model.number="duration"
            class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          >
            <option v-for="d in DURATIONS" :key="d" :value="d">{{ d }} min</option>
          </select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-sm font-medium text-ink" for="provider-select">Provider</label>
          <select
            id="provider-select"
            v-model="provider"
            class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          >
            <option v-for="p in PROVIDERS" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-sm font-medium text-ink" for="facility-modal">Facility</label>
          <select
            id="facility-modal"
            v-model="facility"
            class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          >
            <option v-for="f in FACILITIES" :key="f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-sm font-medium text-ink" for="type-select">Visit type</label>
          <select
            id="type-select"
            v-model="apptType"
            class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          >
            <option v-for="t in APPOINTMENT_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
      </div>

      <div class="flex flex-col gap-1">
        <label class="text-sm font-medium text-ink" for="notes">Notes</label>
        <textarea
          id="notes"
          v-model="notes"
          rows="3"
          class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/30"
          placeholder="Reason for visit, prep instructions, etc."
        />
      </div>

      <p v-if="formError" class="text-xs text-danger-600">{{ formError }}</p>
    </form>

    <template #footer>
      <div class="flex items-center justify-end gap-2">
        <BaseButton variant="secondary" size="sm" @click="emit('update:open', false)">
          Cancel
        </BaseButton>
        <BaseButton
          size="sm"
          :disabled="!canSubmit"
          :loading="submitting"
          @click="submit"
        >
          Schedule
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>
