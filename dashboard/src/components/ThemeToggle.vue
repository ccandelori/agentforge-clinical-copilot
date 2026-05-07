<script setup lang="ts">
import { computed } from 'vue'
import {
  usePreferencesStore,
  type ThemePreference,
} from '@/stores/preferences'

// Three-segment Light / Dark / System control. Renders as a Bootstrap
// button-group of three icon buttons, the active one styled with the
// brand `btn-primary` so the selected state is unambiguous in either
// theme. Wired to usePreferencesStore which persists to localStorage
// and flips <html data-bs-theme>.

interface Segment {
  value: ThemePreference
  label: string
  icon: string // bootstrap-icons class
}

const SEGMENTS: readonly Segment[] = [
  { value: 'light', label: 'Light', icon: 'bi-sun' },
  { value: 'dark', label: 'Dark', icon: 'bi-moon-stars' },
  { value: 'system', label: 'System', icon: 'bi-display' },
] as const

const prefs = usePreferencesStore()

const current = computed<ThemePreference>(() => prefs.theme)

function select(value: ThemePreference): void {
  prefs.setTheme(value)
}
</script>

<template>
  <div
    class="btn-group btn-group-sm"
    role="group"
    aria-label="Theme preference"
  >
    <button
      v-for="seg in SEGMENTS"
      :key="seg.value"
      type="button"
      class="btn"
      :class="
        current === seg.value ? 'btn-primary' : 'btn-outline-secondary'
      "
      :aria-pressed="current === seg.value"
      :title="`${seg.label} theme`"
      @click="select(seg.value)"
    >
      <i class="bi" :class="seg.icon" aria-hidden="true"></i>
      <span class="visually-hidden">{{ seg.label }}</span>
    </button>
  </div>
</template>
