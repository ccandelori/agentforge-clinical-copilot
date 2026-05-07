<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue'
import { usePreferencesStore, type Density } from '@/stores/preferences'

const prefs = usePreferencesStore()

const OPTIONS: ReadonlyArray<{ readonly value: Density; readonly label: string; readonly hint: string }> = [
  { value: 'comfortable', label: 'Comfortable', hint: 'Roomy spacing, easier scanning' },
  { value: 'compact', label: 'Compact', hint: 'More rows on screen' },
]

function setDensity(d: Density): void {
  prefs.setDensity(d)
}
</script>

<template>
  <BaseCard title="Density">
    <div class="flex flex-col gap-5">
      <div
        role="radiogroup"
        aria-label="Density"
        class="inline-flex w-fit rounded-lg border border-line bg-surface-2 p-1"
      >
        <button
          v-for="opt in OPTIONS"
          :key="opt.value"
          type="button"
          role="radio"
          :aria-checked="prefs.density === opt.value"
          class="rounded-md px-3 py-1.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          :class="prefs.density === opt.value
            ? 'bg-surface text-ink shadow-card'
            : 'text-ink-muted hover:text-ink'"
          @click="setDensity(opt.value)"
        >
          {{ opt.label }}
        </button>
      </div>

      <p class="text-xs text-ink-muted">
        {{ OPTIONS.find((o) => o.value === prefs.density)?.hint }}
      </p>

      <div>
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Preview
        </h3>
        <div class="overflow-hidden rounded-lg border border-line">
          <ul class="divide-y divide-line">
            <li
              v-for="row in [
                { name: 'Alvera Beahan', mrn: 'MRN-1001', age: '69 F' },
                { name: 'Hassan Ondricka', mrn: 'MRN-1002', age: '47 M' },
                { name: 'Marisol Reichel', mrn: 'MRN-1003', age: '34 F' },
              ]"
              :key="row.mrn"
              class="flex items-center justify-between bg-surface transition-all"
              :class="prefs.density === 'compact' ? 'px-3 py-1.5' : 'px-4 py-3'"
            >
              <div class="flex items-center gap-3">
                <span
                  class="inline-flex items-center justify-center rounded-full bg-primary-100 font-medium text-primary-700 dark:bg-primary-900 dark:text-primary-200"
                  :class="prefs.density === 'compact' ? 'h-6 w-6 text-[10px]' : 'h-9 w-9 text-sm'"
                >
                  {{ row.name.split(' ').map((p) => p[0]).join('') }}
                </span>
                <div class="min-w-0">
                  <p
                    class="truncate font-medium text-ink"
                    :class="prefs.density === 'compact' ? 'text-xs' : 'text-sm'"
                  >
                    {{ row.name }}
                  </p>
                  <p
                    class="text-ink-muted"
                    :class="prefs.density === 'compact' ? 'text-[10px]' : 'text-xs'"
                  >
                    {{ row.mrn }}
                  </p>
                </div>
              </div>
              <span
                class="text-ink-muted"
                :class="prefs.density === 'compact' ? 'text-[10px]' : 'text-xs'"
              >
                {{ row.age }}
              </span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </BaseCard>
</template>
