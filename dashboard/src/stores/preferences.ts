import { defineStore } from 'pinia'
import { ref } from 'vue'

// User preferences. Today this is just the theme toggle (Light / Dark /
// System). Persists to localStorage and applies via Bootstrap 5.3's native
// `data-bs-theme` attribute on <html>, which flips every bootstrap-vue-next
// component + every utility class scoped to [data-bs-theme="..."] in
// `assets/tokens.css`.

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'dashboard.theme'
const VALID_THEMES: readonly ThemePreference[] = ['light', 'dark', 'system']

function isThemePreference(value: unknown): value is ThemePreference {
  return (
    typeof value === 'string' &&
    (VALID_THEMES as readonly string[]).includes(value)
  )
}

function readStoredTheme(): ThemePreference {
  if (typeof window === 'undefined') return 'system'
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (isThemePreference(raw)) return raw
  } catch {
    // localStorage may be unavailable (privacy modes, SSR shims). Fall
    // through to the default — silent because failure here is expected
    // and the user just gets the system theme.
  }
  return 'system'
}

function persistTheme(theme: ThemePreference): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Ignore — a non-persistent preference is still a valid preference.
  }
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function resolve(theme: ThemePreference): ResolvedTheme {
  if (theme === 'system') return systemPrefersDark() ? 'dark' : 'light'
  return theme
}

function applyToDocument(resolved: ResolvedTheme): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-bs-theme', resolved)
}

export const usePreferencesStore = defineStore('preferences', () => {
  const theme = ref<ThemePreference>('system')
  const resolvedTheme = ref<ResolvedTheme>('light')

  // Listener for OS-level theme changes — only active while theme === 'system'.
  let mediaQuery: MediaQueryList | null = null
  let mediaQueryListener: ((event: MediaQueryListEvent) => void) | null = null

  function detachMediaListener(): void {
    if (mediaQuery !== null && mediaQueryListener !== null) {
      mediaQuery.removeEventListener('change', mediaQueryListener)
    }
    mediaQuery = null
    mediaQueryListener = null
  }

  function attachMediaListener(): void {
    detachMediaListener()
    if (
      typeof window === 'undefined' ||
      typeof window.matchMedia !== 'function'
    ) {
      return
    }
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    mediaQueryListener = (event: MediaQueryListEvent): void => {
      const next: ResolvedTheme = event.matches ? 'dark' : 'light'
      resolvedTheme.value = next
      applyToDocument(next)
    }
    mediaQuery.addEventListener('change', mediaQueryListener)
  }

  function setTheme(next: ThemePreference): void {
    theme.value = next
    persistTheme(next)
    const resolved = resolve(next)
    resolvedTheme.value = resolved
    applyToDocument(resolved)
    if (next === 'system') {
      attachMediaListener()
    } else {
      detachMediaListener()
    }
  }

  function hydrate(): void {
    const stored = readStoredTheme()
    theme.value = stored
    const resolved = resolve(stored)
    resolvedTheme.value = resolved
    applyToDocument(resolved)
    if (stored === 'system') {
      attachMediaListener()
    } else {
      detachMediaListener()
    }
  }

  return { theme, resolvedTheme, setTheme, hydrate }
})
