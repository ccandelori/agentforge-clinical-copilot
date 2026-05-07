<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import BaseCard from '@/components/ui/BaseCard.vue'
import {
  accentTokenMap,
  usePreferencesStore,
  type AccentColor,
  type FontScale,
} from '@/stores/preferences'
import { useUiStore, type ThemeMode } from '@/stores/ui'

import ThemePreviewCard from './ThemePreviewCard.vue'

const ui = useUiStore()
const prefs = usePreferencesStore()

const THEME_MODES: readonly ThemeMode[] = ['light', 'dark', 'system']
const ACCENTS: readonly AccentColor[] = ['teal', 'indigo', 'rose', 'amber', 'emerald']
const FONT_SCALES: readonly FontScale[] = [0.9, 1.0, 1.1, 1.2]

const systemDark = ref<boolean>(false)
let mql: MediaQueryList | undefined
function handleSystemChange(): void {
  systemDark.value = mql?.matches ?? false
}

onMounted(() => {
  if (typeof window === 'undefined' || !window.matchMedia) return
  mql = window.matchMedia('(prefers-color-scheme: dark)')
  systemDark.value = mql.matches
  mql.addEventListener?.('change', handleSystemChange)
})

onBeforeUnmount(() => {
  mql?.removeEventListener?.('change', handleSystemChange)
})

function selectTheme(mode: ThemeMode): void {
  ui.setTheme(mode)
}

function selectAccent(color: AccentColor): void {
  prefs.setAccentColor(color)
}

function selectFontScale(scale: FontScale): void {
  prefs.setFontScale(scale)
}

function fontScaleLabel(scale: FontScale): string {
  return `${Math.round(scale * 100)}%`
}

const fontScaleIndex = computed<number>(() =>
  FONT_SCALES.indexOf(prefs.fontScale),
)

function onSliderInput(event: Event): void {
  const target = event.target as HTMLInputElement
  const idx = Number.parseInt(target.value, 10)
  const next = FONT_SCALES[idx]
  if (next !== undefined) selectFontScale(next)
}
</script>

<template>
  <BaseCard title="Appearance">
    <div class="flex flex-col gap-8">
      <!-- Theme -->
      <div>
        <h3 class="text-sm font-semibold text-ink">Theme</h3>
        <p class="mt-1 text-xs text-ink-muted">
          Choose how the app looks. System matches your OS setting.
        </p>
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <ThemePreviewCard
            v-for="mode in THEME_MODES"
            :key="mode"
            :mode="mode"
            :active="ui.theme === mode"
            :system-dark="systemDark"
            @select="selectTheme"
          />
        </div>
      </div>

      <!-- Accent -->
      <div>
        <h3 class="text-sm font-semibold text-ink">Accent color</h3>
        <p class="mt-1 text-xs text-ink-muted">
          Tints links, focus rings, and small UI accents.
        </p>
        <div class="mt-3 flex flex-wrap items-center gap-3">
          <button
            v-for="color in ACCENTS"
            :key="color"
            type="button"
            class="group relative flex h-10 w-10 items-center justify-center rounded-full border-2 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            :class="prefs.accentColor === color ? 'border-ink' : 'border-transparent hover:border-line'"
            :aria-label="accentTokenMap[color].label"
            :aria-pressed="prefs.accentColor === color"
            :style="{ backgroundColor: accentTokenMap[color].hex }"
            @click="selectAccent(color)"
          >
            <span
              v-if="prefs.accentColor === color"
              class="text-xs font-bold text-white"
              aria-hidden="true"
            >
              ✓
            </span>
          </button>
          <span class="ml-2 text-xs text-ink-muted">
            {{ accentTokenMap[prefs.accentColor].label }}
          </span>
        </div>
      </div>

      <!-- Font scale -->
      <div>
        <h3 class="text-sm font-semibold text-ink">Font scale</h3>
        <p class="mt-1 text-xs text-ink-muted">
          Adjust the base font size. Affects the entire UI.
        </p>
        <div class="mt-3 flex items-center gap-4">
          <input
            type="range"
            min="0"
            :max="FONT_SCALES.length - 1"
            step="1"
            :value="fontScaleIndex"
            class="h-1 w-56 cursor-pointer appearance-none rounded-full bg-surface-2 accent-primary-600"
            aria-label="Font scale"
            @input="onSliderInput"
          />
          <span class="font-mono text-sm tabular-nums text-ink">
            {{ fontScaleLabel(prefs.fontScale) }}
          </span>
        </div>
        <div class="mt-2 flex w-56 justify-between text-[10px] text-ink-muted">
          <span v-for="s in FONT_SCALES" :key="s">{{ fontScaleLabel(s) }}</span>
        </div>
      </div>
    </div>
  </BaseCard>
</template>
