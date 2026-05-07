<script setup lang="ts">
import { ref } from 'vue'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import AutoGrowTextarea from './AutoGrowTextarea.vue'
import LabOrderPicker from './LabOrderPicker.vue'
import type { PlanMedAction, PlanMedItem } from '@/composables/useEncounterDraft'

interface Props {
  plannedMeds: readonly PlanMedItem[]
  plannedLabs: readonly string[]
  referrals: string
  followUpDate: string
  followUpNotes: string
  disabled?: boolean
}

withDefaults(defineProps<Props>(), { disabled: false })

const emit = defineEmits<{
  (e: 'addMed', item: PlanMedItem): void
  (e: 'removeMed', id: string): void
  (e: 'toggleLab', label: string): void
  (e: 'update:referrals', value: string): void
  (e: 'update:followUpDate', value: string): void
  (e: 'update:followUpNotes', value: string): void
}>()

const newMed = ref<string>('')
const newAction = ref<PlanMedAction>('start')

function addMed(): void {
  const name = newMed.value.trim()
  if (!name) return
  const id = `med-${Date.now().toString(36)}`
  emit('addMed', { id, name, action: newAction.value })
  newMed.value = ''
  newAction.value = 'start'
}

function badgeVariant(action: PlanMedAction): 'success' | 'danger' | 'info' {
  switch (action) {
    case 'start':
      return 'success'
    case 'stop':
      return 'danger'
    case 'continue':
      return 'info'
  }
}
</script>

<template>
  <BaseCard title="Plan">
    <div class="flex flex-col gap-5">
      <!-- Medications -->
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Medications
        </h3>
        <div class="flex flex-wrap items-end gap-2">
          <div class="min-w-[14rem] flex-1">
            <BaseInput
              v-model="newMed"
              label="Medication"
              placeholder="e.g. Lisinopril 10 mg PO daily"
              :disabled="disabled"
            />
          </div>
          <label class="flex flex-col gap-1">
            <span class="text-sm font-medium text-ink">Action</span>
            <select
              v-model="newAction"
              :disabled="disabled"
              class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
            >
              <option value="start">Start</option>
              <option value="continue">Continue</option>
              <option value="stop">Stop</option>
            </select>
          </label>
          <BaseButton variant="secondary" :disabled="disabled || !newMed.trim()" @click="addMed">
            Add
          </BaseButton>
        </div>
        <ul v-if="plannedMeds.length > 0" class="mt-3 flex flex-wrap gap-2">
          <li
            v-for="m in plannedMeds"
            :key="m.id"
            class="inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pl-2 pr-1 text-sm"
          >
            <BaseBadge :variant="badgeVariant(m.action)">
              {{ m.action }}
            </BaseBadge>
            <span class="text-ink">{{ m.name }}</span>
            <button
              type="button"
              class="inline-flex h-6 w-6 items-center justify-center rounded-full text-ink-muted hover:bg-danger-100 hover:text-danger-700"
              :disabled="disabled"
              :aria-label="`Remove ${m.name}`"
              @click="emit('removeMed', m.id)"
            >
              ×
            </button>
          </li>
        </ul>
        <p v-else class="mt-2 text-xs text-ink-muted">No medication changes planned.</p>
      </div>

      <!-- Labs -->
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Labs to order
        </h3>
        <LabOrderPicker
          :selected="plannedLabs"
          :disabled="disabled"
          @toggle="emit('toggleLab', $event)"
        />
        <div v-if="plannedLabs.length > 0" class="mt-2 flex flex-wrap gap-1.5">
          <BaseBadge v-for="l in plannedLabs" :key="l" variant="info">
            {{ l }}
          </BaseBadge>
        </div>
      </div>

      <!-- Referrals -->
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Referrals
        </h3>
        <AutoGrowTextarea
          :model-value="referrals"
          placeholder="e.g. Cardiology — atypical chest pain, please evaluate."
          :rows="2"
          :disabled="disabled"
          @update:model-value="emit('update:referrals', $event)"
        />
      </div>

      <!-- Follow-up -->
      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Follow-up
        </h3>
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label class="flex flex-col gap-1">
            <span class="text-sm font-medium text-ink">Return on</span>
            <input
              type="date"
              :value="followUpDate"
              :disabled="disabled"
              class="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
              @input="emit('update:followUpDate', ($event.target as HTMLInputElement).value)"
            />
          </label>
          <div class="sm:col-span-2">
            <AutoGrowTextarea
              label="Notes"
              :model-value="followUpNotes"
              placeholder="Reason for follow-up, what to bring, etc."
              :rows="2"
              :disabled="disabled"
              @update:model-value="emit('update:followUpNotes', $event)"
            />
          </div>
        </div>
      </div>
    </div>
  </BaseCard>
</template>
