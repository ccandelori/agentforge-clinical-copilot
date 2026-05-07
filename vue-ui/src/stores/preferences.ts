import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

/**
 * User preferences (Wave 2g).
 *
 * Owns presentation-layer settings that are NOT theme mode (which lives in
 * `useUiStore()`). All values are persisted to localStorage under
 * `vue-ui.preferences` and applied to `<html>` on hydrate.
 */

export type AccentColor = 'teal' | 'indigo' | 'rose' | 'amber' | 'emerald'
export type FontScale = 0.9 | 1.0 | 1.1 | 1.2
export type Density = 'comfortable' | 'compact'

export interface NotificationPrefs {
  readonly browser: boolean
  readonly email: boolean
  readonly afterHours: boolean
}

export interface PreferencesState {
  readonly accentColor: AccentColor
  readonly fontScale: FontScale
  readonly density: Density
  readonly notifications: NotificationPrefs
}

const STORAGE_KEY = 'vue-ui.preferences'

const DEFAULTS: PreferencesState = {
  accentColor: 'teal',
  fontScale: 1.0,
  density: 'comfortable',
  notifications: {
    browser: false,
    email: true,
    afterHours: false,
  },
}

const ACCENT_COLORS: readonly AccentColor[] = [
  'teal',
  'indigo',
  'rose',
  'amber',
  'emerald',
]
const FONT_SCALES: readonly FontScale[] = [0.9, 1.0, 1.1, 1.2]
const DENSITIES: readonly Density[] = ['comfortable', 'compact']

/**
 * Visible CSS color values per accent. The hex value is exposed as the
 * `--accent` CSS variable, while the RGB triplet drives the `--accent-rgb`
 * variable already referenced by Tailwind tokens (see tailwind.config.ts).
 */
export interface AccentToken {
  readonly hex: string
  readonly rgb: string // "R G B" — space-separated for `rgb(var() / <alpha>)`
  readonly label: string
}

export const accentTokenMap: Readonly<Record<AccentColor, AccentToken>> = {
  teal: { hex: '#0d9488', rgb: '13 148 136', label: 'Teal' },
  indigo: { hex: '#4f46e5', rgb: '79 70 229', label: 'Indigo' },
  rose: { hex: '#e11d48', rgb: '225 29 72', label: 'Rose' },
  amber: { hex: '#d97706', rgb: '217 119 6', label: 'Amber' },
  emerald: { hex: '#059669', rgb: '5 150 105', label: 'Emerald' },
}

function isAccentColor(v: unknown): v is AccentColor {
  return typeof v === 'string' && (ACCENT_COLORS as readonly string[]).includes(v)
}

function isFontScale(v: unknown): v is FontScale {
  return typeof v === 'number' && (FONT_SCALES as readonly number[]).includes(v)
}

function isDensity(v: unknown): v is Density {
  return typeof v === 'string' && (DENSITIES as readonly string[]).includes(v)
}

function isNotificationPrefs(v: unknown): v is NotificationPrefs {
  if (typeof v !== 'object' || v === null) return false
  const o = v as Record<string, unknown>
  return (
    typeof o.browser === 'boolean'
    && typeof o.email === 'boolean'
    && typeof o.afterHours === 'boolean'
  )
}

function readStored(): PreferencesState {
  if (typeof localStorage === 'undefined') return DEFAULTS
  const raw = localStorage.getItem(STORAGE_KEY)
  if (raw === null) return DEFAULTS
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return DEFAULTS
    const o = parsed as Record<string, unknown>
    return {
      accentColor: isAccentColor(o.accentColor) ? o.accentColor : DEFAULTS.accentColor,
      fontScale: isFontScale(o.fontScale) ? o.fontScale : DEFAULTS.fontScale,
      density: isDensity(o.density) ? o.density : DEFAULTS.density,
      notifications: isNotificationPrefs(o.notifications)
        ? o.notifications
        : DEFAULTS.notifications,
    }
  } catch {
    return DEFAULTS
  }
}

function applyAccent(color: AccentColor): void {
  if (typeof document === 'undefined') return
  const token = accentTokenMap[color]
  document.documentElement.style.setProperty('--accent', token.hex)
  document.documentElement.style.setProperty('--accent-rgb', token.rgb)
}

function applyFontScale(scale: FontScale): void {
  if (typeof document === 'undefined') return
  document.documentElement.style.fontSize = `${Math.round(scale * 100)}%`
}

function applyDensity(density: Density): void {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.density = density
}

export const usePreferencesStore = defineStore('preferences', () => {
  const initial = readStored()

  const accentColor = ref<AccentColor>(initial.accentColor)
  const fontScale = ref<FontScale>(initial.fontScale)
  const density = ref<Density>(initial.density)
  const notifications = ref<NotificationPrefs>(initial.notifications)
  const hydrated = ref<boolean>(false)

  const accentToken = computed<AccentToken>(() => accentTokenMap[accentColor.value])

  function persist(): void {
    if (typeof localStorage === 'undefined') return
    const snapshot: PreferencesState = {
      accentColor: accentColor.value,
      fontScale: fontScale.value,
      density: density.value,
      notifications: notifications.value,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot))
  }

  function setAccentColor(next: AccentColor): void {
    accentColor.value = next
  }

  function setFontScale(next: FontScale): void {
    fontScale.value = next
  }

  function setDensity(next: Density): void {
    density.value = next
  }

  function setNotification(key: keyof NotificationPrefs, value: boolean): void {
    notifications.value = { ...notifications.value, [key]: value }
  }

  /**
   * Reflect current state into the DOM and start watchers that persist
   * subsequent changes. Idempotent.
   */
  function hydrate(): void {
    if (hydrated.value) return
    hydrated.value = true

    applyAccent(accentColor.value)
    applyFontScale(fontScale.value)
    applyDensity(density.value)

    watch(accentColor, (next) => {
      applyAccent(next)
      persist()
    })
    watch(fontScale, (next) => {
      applyFontScale(next)
      persist()
    })
    watch(density, (next) => {
      applyDensity(next)
      persist()
    })
    watch(notifications, () => {
      persist()
    }, { deep: true })
  }

  return {
    accentColor,
    fontScale,
    density,
    notifications,
    accentToken,
    setAccentColor,
    setFontScale,
    setDensity,
    setNotification,
    hydrate,
  }
})
