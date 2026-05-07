<script setup lang="ts">
import { ref } from 'vue'

import BaseCard from '@/components/ui/BaseCard.vue'
import type { AttachmentItem } from '@/composables/useEncounterDraft'

interface Props {
  attachments: readonly AttachmentItem[]
  disabled?: boolean
}

withDefaults(defineProps<Props>(), { disabled: false })

const dragOver = ref<boolean>(false)

function fmtKb(n: number): string {
  if (n < 1024) return `${n} KB`
  return `${(n / 1024).toFixed(1)} MB`
}

function fmtDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
</script>

<template>
  <BaseCard title="Attachments">
    <div class="flex flex-col gap-3">
      <div
        class="flex flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-8 text-center text-sm transition-colors"
        :class="
          dragOver
            ? 'border-primary-500 bg-primary-50 text-primary-700 dark:bg-primary-900/20'
            : 'border-line bg-surface-2 text-ink-muted'
        "
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="dragOver = false"
      >
        <p class="font-medium text-ink">Drop files here to attach</p>
        <p class="text-xs">PDF, JPG, PNG up to 25 MB · drag &amp; drop or click to browse</p>
      </div>

      <ul v-if="attachments.length > 0" class="divide-y divide-line">
        <li
          v-for="a in attachments"
          :key="a.id"
          class="flex items-center justify-between gap-3 py-2.5"
        >
          <div class="min-w-0 flex items-center gap-3">
            <span
              class="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-surface-2 text-xs font-mono text-ink-muted"
              aria-hidden="true"
            >
              {{ a.filename.split('.').pop()?.toUpperCase() ?? 'FILE' }}
            </span>
            <div class="min-w-0">
              <p class="truncate text-sm font-medium text-ink">{{ a.filename }}</p>
              <p class="text-xs text-ink-muted">
                {{ fmtKb(a.sizeKb) }} · added {{ fmtDate(a.uploadedAt) }}
              </p>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </BaseCard>
</template>
