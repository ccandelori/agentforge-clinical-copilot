import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEYS = {
  theme: 'oe-vue.theme',
  sidebar: 'oe-vue.sidebar.collapsed',
} as const

function readStoredTheme(): ThemeMode {
  if (typeof localStorage === 'undefined') return 'system'
  const v = localStorage.getItem(STORAGE_KEYS.theme)
  if (v === 'light' || v === 'dark' || v === 'system') return v
  return 'system'
}

function readStoredSidebar(): boolean {
  if (typeof localStorage === 'undefined') return false
  return localStorage.getItem(STORAGE_KEYS.sidebar) === '1'
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function applyDarkClass(isDark: boolean): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.classList.toggle('dark', isDark)
}

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref<boolean>(readStoredSidebar())
  const theme = ref<ThemeMode>(readStoredTheme())
  const agentDrawerOpen = ref<boolean>(false)

  const resolvedDark = computed<boolean>(() => {
    if (theme.value === 'dark') return true
    if (theme.value === 'light') return false
    return systemPrefersDark()
  })

  function setTheme(next: ThemeMode): void {
    theme.value = next
  }

  function toggleTheme(): void {
    theme.value = resolvedDark.value ? 'light' : 'dark'
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function openAgentDrawer(): void {
    agentDrawerOpen.value = true
  }

  function closeAgentDrawer(): void {
    agentDrawerOpen.value = false
  }

  function toggleAgentDrawer(): void {
    agentDrawerOpen.value = !agentDrawerOpen.value
  }

  /**
   * Called once at app boot to attach watchers and reflect state into the
   * DOM / storage. Safe to call multiple times.
   */
  function hydrate(): void {
    applyDarkClass(resolvedDark.value)

    watch(theme, (next) => {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(STORAGE_KEYS.theme, next)
      }
      applyDarkClass(resolvedDark.value)
    })

    watch(sidebarCollapsed, (next) => {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(STORAGE_KEYS.sidebar, next ? '1' : '0')
      }
    })

    // Track system theme changes when in 'system' mode.
    if (typeof window !== 'undefined' && window.matchMedia) {
      const mql = window.matchMedia('(prefers-color-scheme: dark)')
      const onChange = (): void => {
        if (theme.value === 'system') applyDarkClass(resolvedDark.value)
      }
      // Modern browsers
      mql.addEventListener?.('change', onChange)
    }
  }

  return {
    sidebarCollapsed,
    theme,
    agentDrawerOpen,
    resolvedDark,
    setTheme,
    toggleTheme,
    toggleSidebar,
    openAgentDrawer,
    closeAgentDrawer,
    toggleAgentDrawer,
    hydrate,
  }
})
