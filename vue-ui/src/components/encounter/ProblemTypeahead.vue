<script setup lang="ts">
import { computed, ref } from 'vue'

interface IcdEntry {
  readonly code: string
  readonly description: string
}

interface Props {
  disabled?: boolean
  excludeCodes?: readonly string[]
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  excludeCodes: () => [],
})

const emit = defineEmits<{
  (e: 'pick', entry: IcdEntry): void
}>()

const ICD_LIST: readonly IcdEntry[] = [
  { code: 'I10', description: 'Essential hypertension' },
  { code: 'E11.9', description: 'Type 2 diabetes mellitus, uncomplicated' },
  { code: 'E78.5', description: 'Hyperlipidemia, unspecified' },
  { code: 'J45.909', description: 'Asthma, unspecified, uncomplicated' },
  { code: 'F41.1', description: 'Generalized anxiety disorder' },
  { code: 'F32.9', description: 'Major depressive disorder, unspecified' },
  { code: 'M54.5', description: 'Low back pain' },
  { code: 'M25.561', description: 'Pain in right knee' },
  { code: 'K21.9', description: 'Gastroesophageal reflux disease' },
  { code: 'G47.00', description: 'Insomnia, unspecified' },
  { code: 'G43.909', description: 'Migraine, unspecified, not intractable' },
  { code: 'N39.0', description: 'Urinary tract infection, site not specified' },
  { code: 'J06.9', description: 'Acute upper respiratory infection, unspecified' },
  { code: 'J20.9', description: 'Acute bronchitis, unspecified' },
  { code: 'J18.9', description: 'Pneumonia, unspecified organism' },
  { code: 'R51.9', description: 'Headache, unspecified' },
  { code: 'R10.9', description: 'Unspecified abdominal pain' },
  { code: 'R05.9', description: 'Cough, unspecified' },
  { code: 'R07.9', description: 'Chest pain, unspecified' },
  { code: 'R42', description: 'Dizziness and giddiness' },
  { code: 'R53.83', description: 'Other fatigue' },
  { code: 'E66.9', description: 'Obesity, unspecified' },
  { code: 'E03.9', description: 'Hypothyroidism, unspecified' },
  { code: 'D64.9', description: 'Anemia, unspecified' },
  { code: 'I25.10', description: 'Atherosclerotic heart disease without angina' },
  { code: 'I48.91', description: 'Atrial fibrillation, unspecified' },
  { code: 'I50.9', description: 'Heart failure, unspecified' },
  { code: 'N18.3', description: 'Chronic kidney disease, stage 3 (moderate)' },
  { code: 'L30.9', description: 'Dermatitis, unspecified' },
  { code: 'Z00.00', description: 'General adult medical exam, no abnormal findings' },
]

const query = ref<string>('')
const focused = ref<boolean>(false)

const matches = computed<readonly IcdEntry[]>(() => {
  const q = query.value.trim().toLowerCase()
  const excluded = new Set(props.excludeCodes)
  if (!q) {
    return ICD_LIST.filter((e) => !excluded.has(e.code)).slice(0, 6)
  }
  return ICD_LIST.filter((e) => {
    if (excluded.has(e.code)) return false
    return (
      e.code.toLowerCase().includes(q) || e.description.toLowerCase().includes(q)
    )
  }).slice(0, 8)
})

function pick(entry: IcdEntry): void {
  emit('pick', entry)
  query.value = ''
}

function onBlur(): void {
  // Delay so click on dropdown registers first.
  window.setTimeout(() => {
    focused.value = false
  }, 120)
}
</script>

<template>
  <div class="relative">
    <input
      v-model="query"
      type="text"
      placeholder="Search ICD-10 code or description…"
      :disabled="disabled"
      class="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 disabled:cursor-not-allowed disabled:opacity-50"
      @focus="focused = true"
      @blur="onBlur"
    />
    <div
      v-if="focused && matches.length > 0"
      class="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-line bg-surface shadow-card-lg"
    >
      <button
        v-for="m in matches"
        :key="m.code"
        type="button"
        class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-ink hover:bg-surface-2"
        @mousedown.prevent="pick(m)"
      >
        <span class="truncate">{{ m.description }}</span>
        <span class="shrink-0 font-mono text-xs text-ink-muted">{{ m.code }}</span>
      </button>
    </div>
  </div>
</template>
