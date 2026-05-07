<script setup lang="ts">
import { computed } from 'vue'

import type { ThemeMode } from '@/stores/ui'

interface Props {
  mode: ThemeMode
  active: boolean
  systemDark: boolean
}

const props = defineProps<Props>()
defineEmits<{
  (e: 'select', mode: ThemeMode): void
}>()

const isDark = computed<boolean>(() => {
  if (props.mode === 'dark') return true
  if (props.mode === 'light') return false
  return props.systemDark
})

const label = computed<string>(() => {
  switch (props.mode) {
    case 'light':
      return 'Light'
    case 'dark':
      return 'Dark'
    case 'system':
      return 'System'
  }
})

// Inline styles so the preview shows that theme regardless of the page's
// current theme. Use color literals (not Tailwind tokens) on purpose.
const surface = computed<string>(() => (isDark.value ? '#18181b' : '#ffffff'))
const surface2 = computed<string>(() => (isDark.value ? '#27272a' : '#f9fafb'))
const text = computed<string>(() => (isDark.value ? '#f4f4f5' : '#18181b'))
const textMuted = computed<string>(() => (isDark.value ? '#a1a1aa' : '#71717a'))
const border = computed<string>(() => (isDark.value ? '#3f3f46' : '#e4e4e7'))
</script>

<template>
  <button
    type="button"
    class="group flex flex-col overflow-hidden rounded-xl border-2 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
    :class="active ? 'border-primary-500 shadow-card-lg' : 'border-line hover:border-ink-muted'"
    :aria-pressed="active"
    @click="$emit('select', mode)"
  >
    <div
      class="aspect-[5/3] w-full p-2.5"
      :style="{ backgroundColor: surface2 }"
    >
      <div
        class="h-full w-full rounded-md border p-2"
        :style="{ backgroundColor: surface, borderColor: border }"
      >
        <div class="flex items-center gap-1.5">
          <span class="h-1.5 w-6 rounded-full" :style="{ backgroundColor: 'rgb(var(--accent-rgb))' }" />
          <span class="h-1.5 w-10 rounded-full" :style="{ backgroundColor: textMuted, opacity: 0.4 }" />
        </div>
        <div class="mt-2 space-y-1">
          <div class="h-1 w-full rounded-full" :style="{ backgroundColor: textMuted, opacity: 0.25 }" />
          <div class="h-1 w-5/6 rounded-full" :style="{ backgroundColor: textMuted, opacity: 0.25 }" />
          <div class="h-1 w-3/4 rounded-full" :style="{ backgroundColor: textMuted, opacity: 0.25 }" />
        </div>
        <div
          class="mt-2 h-3 w-12 rounded"
          :style="{ backgroundColor: 'rgb(var(--accent-rgb))', opacity: 0.85 }"
        />
      </div>
    </div>
    <div
      class="flex items-center justify-between border-t px-3 py-2"
      :style="{ borderColor: border, backgroundColor: surface, color: text }"
    >
      <span class="text-sm font-medium">{{ label }}</span>
      <span
        v-if="active"
        class="inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary-600 text-[10px] font-bold text-white"
        aria-hidden="true"
      >
        ✓
      </span>
    </div>
  </button>
</template>
