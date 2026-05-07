<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  getEncounters,
  getPatient,
  type Encounter,
  type Patient,
} from '@/api/mock'
import BaseSpinner from '@/components/ui/BaseSpinner.vue'

import EncounterHeader from '@/components/encounter/EncounterHeader.vue'
import EncounterSectionNav from '@/components/encounter/EncounterSectionNav.vue'
import SubjectiveSection from '@/components/encounter/SubjectiveSection.vue'
import ObjectiveSection from '@/components/encounter/ObjectiveSection.vue'
import AssessmentSection from '@/components/encounter/AssessmentSection.vue'
import PlanSection from '@/components/encounter/PlanSection.vue'
import OrdersSection from '@/components/encounter/OrdersSection.vue'
import AttachmentsSection from '@/components/encounter/AttachmentsSection.vue'
import BaseCard from '@/components/ui/BaseCard.vue'

import {
  useEncounterDraft,
  type AssessmentItem,
  type EncounterVitalsInput,
  type PlanMedItem,
} from '@/composables/useEncounterDraft'

interface Props {
  id: string
}

const props = defineProps<Props>()
const router = useRouter()

interface NavItem {
  readonly id: string
  readonly label: string
}

const navItems: readonly NavItem[] = [
  { id: 'subjective', label: 'Subjective' },
  { id: 'objective', label: 'Objective' },
  { id: 'assessment', label: 'Assessment' },
  { id: 'plan', label: 'Plan' },
  { id: 'vitals-summary', label: 'Vitals' },
  { id: 'orders', label: 'Orders' },
  { id: 'attachments', label: 'Attachments' },
]

const activeId = ref<string>(navItems[0]?.id ?? 'subjective')

const encounter = ref<Encounter | null>(null)
const patient = ref<Patient | null>(null)
const loadError = ref<string | null>(null)
const loading = ref<boolean>(true)

// Encounter id format from mock: `enc-p-XXXX-NNNN`. Extract patient id.
function extractPatientId(encounterId: string): string | null {
  const match = encounterId.match(/^enc-(p-\d{4})-\d+$/)
  return match ? match[1] ?? null : null
}

const draftCtx = useEncounterDraft(props.id)

const requiredFilled = computed<boolean>(() => {
  const d = draftCtx.draft
  return (
    d.chiefComplaint.trim().length > 0 &&
    d.hpi.trim().length > 0 &&
    d.problems.length > 0
  )
})

const wordCount = computed<number>(() => {
  const d = draftCtx.draft
  const fields = [
    d.chiefComplaint,
    d.hpi,
    d.examHeart,
    d.examLungs,
    d.examAbdomen,
    d.examNeuro,
    d.referrals,
    d.followUpNotes,
  ]
  return fields
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .reduce((sum, s) => sum + s.split(/\s+/).length, 0)
})

const lastSavedLabel = computed<string>(() => {
  const ts = draftCtx.lastSavedAt.value
  if (!ts) return 'Not saved yet'
  const d = new Date(ts)
  return `Saved ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' })}`
})

// ---------------- IntersectionObserver section tracking ----------------
let observer: IntersectionObserver | null = null
const sectionRefs = new Map<string, HTMLElement>()

function registerSection(id: string, el: Element | null): void {
  if (!(el instanceof HTMLElement)) return
  sectionRefs.set(id, el)
  if (observer) observer.observe(el)
}

function setupObserver(): void {
  if (typeof IntersectionObserver === 'undefined') return
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
      const top = visible[0]
      if (top && top.target instanceof HTMLElement && top.target.dataset.sectionId) {
        activeId.value = top.target.dataset.sectionId
      }
    },
    { rootMargin: '-30% 0px -55% 0px', threshold: [0, 0.25, 0.5, 0.75, 1] },
  )
  for (const el of sectionRefs.values()) observer.observe(el)
}

function scrollTo(id: string): void {
  const el = sectionRefs.get(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeId.value = id
}

// ---------------- Lifecycle ----------------
onMounted(async () => {
  const pid = extractPatientId(props.id)
  if (!pid) {
    loadError.value = 'Could not parse encounter id.'
    loading.value = false
    return
  }
  try {
    const [pt, encs] = await Promise.all([getPatient(pid), getEncounters(pid)])
    if (!pt) {
      loadError.value = 'Patient not found.'
      loading.value = false
      return
    }
    patient.value = pt
    const found = encs.find((e) => e.id === props.id)
    encounter.value = found ?? {
      id: props.id,
      patientId: pid,
      date: new Date().toISOString(),
      type: 'Office Visit',
      providerName: pt.pcp ?? 'Dr. Patel',
      reason: 'New visit',
      status: 'in-progress',
    }
  } catch {
    loadError.value = 'Failed to load encounter.'
  } finally {
    loading.value = false
  }
  setupObserver()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  sectionRefs.clear()
})

// Re-attach observer when encounter resolves & sections render.
watch(loading, (isLoading) => {
  if (!isLoading) {
    // setupObserver is idempotent enough — but only call if not yet created.
    if (!observer) setupObserver()
  }
})

// ---------------- Mutators ----------------
function updateVitals(v: EncounterVitalsInput): void {
  draftCtx.draft.vitals = v
}

function addProblem(item: AssessmentItem): void {
  draftCtx.draft.problems = [...draftCtx.draft.problems, item]
}

function removeProblem(id: string): void {
  draftCtx.draft.problems = draftCtx.draft.problems.filter((p) => p.id !== id)
}

function addMed(item: PlanMedItem): void {
  draftCtx.draft.plannedMeds = [...draftCtx.draft.plannedMeds, item]
}

function removeMed(id: string): void {
  draftCtx.draft.plannedMeds = draftCtx.draft.plannedMeds.filter((m) => m.id !== id)
}

function toggleLab(label: string): void {
  const has = draftCtx.draft.plannedLabs.includes(label)
  draftCtx.draft.plannedLabs = has
    ? draftCtx.draft.plannedLabs.filter((l) => l !== label)
    : [...draftCtx.draft.plannedLabs, label]
}

function removeOrder(id: string): void {
  draftCtx.draft.pendingOrders = draftCtx.draft.pendingOrders.filter((o) => o.id !== id)
}

function onSign(): void {
  if (!requiredFilled.value) return
  draftCtx.finalize()
}

function onSave(): void {
  draftCtx.saveNow()
}

function goBack(): void {
  if (patient.value) {
    void router.push({ name: 'patient-dashboard', params: { id: patient.value.id } })
  } else {
    void router.push({ name: 'patients' })
  }
}

// Vitals summary: format only filled fields
const vitalsSummary = computed<readonly { label: string; value: string }[]>(() => {
  const v = draftCtx.draft.vitals
  const items: { label: string; value: string }[] = []
  if (v.heartRate) items.push({ label: 'HR', value: `${v.heartRate} bpm` })
  if (v.systolic && v.diastolic) {
    items.push({ label: 'BP', value: `${v.systolic}/${v.diastolic} mmHg` })
  } else if (v.systolic) {
    items.push({ label: 'SBP', value: `${v.systolic} mmHg` })
  }
  if (v.tempC) items.push({ label: 'Temp', value: `${v.tempC} °C` })
  if (v.respRate) items.push({ label: 'RR', value: `${v.respRate}/min` })
  if (v.spo2) items.push({ label: 'SpO₂', value: `${v.spo2}%` })
  if (v.weightKg) items.push({ label: 'Weight', value: `${v.weightKg} kg` })
  if (v.heightCm) items.push({ label: 'Height', value: `${v.heightCm} cm` })
  return items
})
</script>

<template>
  <div class="px-4 pb-24">
    <!-- Loading / error states -->
    <div v-if="loading" class="flex items-center justify-center py-24">
      <BaseSpinner />
    </div>
    <div
      v-else-if="loadError || !encounter || !patient"
      class="mx-auto max-w-md rounded-xl border border-line bg-surface p-6 text-center"
    >
      <p class="text-sm font-medium text-ink">{{ loadError ?? 'Encounter not found.' }}</p>
      <button
        type="button"
        class="mt-3 text-sm text-primary-700 hover:underline"
        @click="goBack"
      >
        Go back
      </button>
    </div>

    <template v-else>
      <EncounterHeader
        :encounter="encounter"
        :patient="patient"
        :signed-at="draftCtx.signedAt.value"
        :can-finalize="requiredFilled"
        @sign="onSign"
        @save="onSave"
      />

      <div class="grid grid-cols-12 gap-6">
        <!-- Left rail nav -->
        <aside class="col-span-12 lg:col-span-3">
          <EncounterSectionNav
            :items="navItems"
            :active-id="activeId"
            @select="scrollTo"
          />
        </aside>

        <!-- Main column -->
        <div class="col-span-12 flex flex-col gap-6 lg:col-span-9">
          <section
            :ref="(el) => registerSection('subjective', el as Element | null)"
            data-section-id="subjective"
            class="scroll-mt-32"
          >
            <SubjectiveSection
              :chief-complaint="draftCtx.draft.chiefComplaint"
              :hpi="draftCtx.draft.hpi"
              :disabled="draftCtx.signedAt.value !== null"
              @update:chief-complaint="draftCtx.draft.chiefComplaint = $event"
              @update:hpi="draftCtx.draft.hpi = $event"
            />
          </section>

          <section
            :ref="(el) => registerSection('objective', el as Element | null)"
            data-section-id="objective"
            class="scroll-mt-32"
          >
            <ObjectiveSection
              :vitals="draftCtx.draft.vitals"
              :exam-heart="draftCtx.draft.examHeart"
              :exam-lungs="draftCtx.draft.examLungs"
              :exam-abdomen="draftCtx.draft.examAbdomen"
              :exam-neuro="draftCtx.draft.examNeuro"
              :disabled="draftCtx.signedAt.value !== null"
              @update:vitals="updateVitals"
              @update:exam-heart="draftCtx.draft.examHeart = $event"
              @update:exam-lungs="draftCtx.draft.examLungs = $event"
              @update:exam-abdomen="draftCtx.draft.examAbdomen = $event"
              @update:exam-neuro="draftCtx.draft.examNeuro = $event"
            />
          </section>

          <section
            :ref="(el) => registerSection('assessment', el as Element | null)"
            data-section-id="assessment"
            class="scroll-mt-32"
          >
            <AssessmentSection
              :problems="draftCtx.draft.problems"
              :disabled="draftCtx.signedAt.value !== null"
              @add="addProblem"
              @remove="removeProblem"
            />
          </section>

          <section
            :ref="(el) => registerSection('plan', el as Element | null)"
            data-section-id="plan"
            class="scroll-mt-32"
          >
            <PlanSection
              :planned-meds="draftCtx.draft.plannedMeds"
              :planned-labs="draftCtx.draft.plannedLabs"
              :referrals="draftCtx.draft.referrals"
              :follow-up-date="draftCtx.draft.followUpDate"
              :follow-up-notes="draftCtx.draft.followUpNotes"
              :disabled="draftCtx.signedAt.value !== null"
              @add-med="addMed"
              @remove-med="removeMed"
              @toggle-lab="toggleLab"
              @update:referrals="draftCtx.draft.referrals = $event"
              @update:follow-up-date="draftCtx.draft.followUpDate = $event"
              @update:follow-up-notes="draftCtx.draft.followUpNotes = $event"
            />
          </section>

          <section
            :ref="(el) => registerSection('vitals-summary', el as Element | null)"
            data-section-id="vitals-summary"
            class="scroll-mt-32"
          >
            <BaseCard title="Vitals (summary)">
              <div v-if="vitalsSummary.length === 0" class="text-sm text-ink-muted">
                No vitals entered yet.
              </div>
              <dl v-else class="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
                <div v-for="v in vitalsSummary" :key="v.label">
                  <dt class="text-[11px] uppercase tracking-wide text-ink-muted">
                    {{ v.label }}
                  </dt>
                  <dd class="text-sm font-medium text-ink">{{ v.value }}</dd>
                </div>
              </dl>
            </BaseCard>
          </section>

          <section
            :ref="(el) => registerSection('orders', el as Element | null)"
            data-section-id="orders"
            class="scroll-mt-32"
          >
            <OrdersSection
              :orders="draftCtx.draft.pendingOrders"
              :disabled="draftCtx.signedAt.value !== null"
              @remove="removeOrder"
            />
          </section>

          <section
            :ref="(el) => registerSection('attachments', el as Element | null)"
            data-section-id="attachments"
            class="scroll-mt-32"
          >
            <AttachmentsSection
              :attachments="draftCtx.draft.attachments"
              :disabled="draftCtx.signedAt.value !== null"
            />
          </section>
        </div>
      </div>

      <!-- Footer status bar -->
      <div
        class="pointer-events-none fixed bottom-4 right-4 z-30 flex items-center gap-3 rounded-full border border-line bg-surface/95 px-4 py-2 text-xs shadow-card backdrop-blur"
      >
        <span class="text-ink-muted">{{ wordCount }} words</span>
        <span class="h-3 w-px bg-line" aria-hidden="true" />
        <span
          :class="
            draftCtx.isDirty.value
              ? 'text-warning-600'
              : draftCtx.lastSavedAt.value
                ? 'text-success-600'
                : 'text-ink-muted'
          "
        >
          {{ draftCtx.isDirty.value ? 'Saving…' : lastSavedLabel }}
        </span>
        <!--
          HIPAA: drafts persist to sessionStorage, not localStorage — they
          live only for the current browser session. Surface that here so
          clinicians don't expect cross-session recovery.
        -->
        <span
          class="pointer-events-auto inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-[10px] font-semibold text-ink-muted"
          role="img"
          aria-label="Drafts auto-save to this session only"
          title="Drafts auto-save to this session only. Closing this tab clears the draft."
        >
          i
        </span>
        <span v-if="draftCtx.signedAt.value" class="h-3 w-px bg-line" aria-hidden="true" />
        <span v-if="draftCtx.signedAt.value" class="text-success-700">Signed</span>
      </div>
    </template>
  </div>
</template>
