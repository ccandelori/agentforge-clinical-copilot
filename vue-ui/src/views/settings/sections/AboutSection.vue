<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue'

interface AboutItem {
  readonly label: string
  readonly value: string
}

const VERSION = '1.0.0-vue'
const BUILD_SHA = 'dev-greenfield'
const SOURCE_URL = 'https://github.com/openemr/openemr'

const ITEMS: readonly AboutItem[] = [
  { label: 'Version', value: VERSION },
  { label: 'Build', value: BUILD_SHA },
  { label: 'Frontend', value: 'Vue 3 · TypeScript · Tailwind' },
  { label: 'Licenses', value: '128 dependencies' },
]
</script>

<template>
  <BaseCard title="About">
    <div class="flex flex-col gap-4">
      <dl class="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <div v-for="item in ITEMS" :key="item.label" class="flex items-baseline justify-between gap-4 sm:flex-col sm:items-start sm:gap-1">
          <dt class="text-xs uppercase tracking-wide text-ink-muted">{{ item.label }}</dt>
          <dd class="font-mono text-sm text-ink">{{ item.value }}</dd>
        </div>
      </dl>

      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-4 text-xs">
        <a
          :href="SOURCE_URL"
          target="_blank"
          rel="noopener noreferrer"
          class="font-medium text-primary-600 hover:underline dark:text-primary-400"
        >
          View source
        </a>
        <a
          :href="`${SOURCE_URL}/blob/master/LICENSE`"
          target="_blank"
          rel="noopener noreferrer"
          class="font-medium text-primary-600 hover:underline dark:text-primary-400"
        >
          Licenses
        </a>
        <span class="text-ink-muted">© OpenEMR Foundation. GPL-3.0.</span>
      </div>
    </div>
  </BaseCard>
</template>
