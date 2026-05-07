<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

interface NavItem {
  readonly to: string
  readonly label: string
  readonly icon: string
}

const ui = useUiStore()
const auth = useAuthStore()
const router = useRouter()

const search = ref<string>('')
const userMenuOpen = ref<boolean>(false)
const userMenuRef = ref<HTMLElement | null>(null)

function onDocClick(ev: MouseEvent): void {
  if (!userMenuOpen.value) return
  const target = ev.target as Node | null
  if (target && userMenuRef.value && !userMenuRef.value.contains(target)) {
    userMenuOpen.value = false
  }
}

onMounted(() => document.addEventListener('mousedown', onDocClick))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))

const navItems: readonly NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/patients', label: 'Patients', icon: 'patients' },
  { to: '/calendar', label: 'Calendar', icon: 'calendar' },
  { to: '/encounters/0', label: 'Encounters', icon: 'encounters' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

const themeIcon = computed<string>(() => (ui.resolvedDark ? 'sun' : 'moon'))
const themeLabel = computed<string>(() =>
  ui.resolvedDark ? 'Switch to light mode' : 'Switch to dark mode',
)

function onSearchSubmit(): void {
  if (!search.value.trim()) return
  router.push({ name: 'patients', query: { q: search.value.trim() } })
}

async function onLogout(): Promise<void> {
  await auth.signOut()
}
</script>

<template>
  <div class="flex h-full min-h-screen w-full bg-surface-2 text-ink">
    <!-- Sidebar -->
    <aside
      class="flex shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200 ease-out"
      :class="ui.sidebarCollapsed ? 'w-16' : 'w-60'"
      aria-label="Primary navigation"
    >
      <div class="flex h-14 items-center gap-2 border-b border-line px-3">
        <div
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-600 text-white"
          aria-hidden="true"
        >
          <span class="font-semibold">O</span>
        </div>
        <span
          v-if="!ui.sidebarCollapsed"
          class="truncate text-sm font-semibold tracking-tight"
        >
          OpenEMR
        </span>
        <button
          type="button"
          class="ml-auto rounded-md p-1.5 text-ink-muted hover:bg-surface-2 hover:text-ink"
          :aria-label="ui.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          @click="ui.toggleSidebar"
        >
          <span aria-hidden="true">{{ ui.sidebarCollapsed ? '»' : '«' }}</span>
        </button>
      </div>

      <nav class="flex-1 overflow-y-auto px-2 py-3">
        <ul class="flex flex-col gap-1">
          <li v-for="item in navItems" :key="item.to">
            <RouterLink
              :to="item.to"
              class="group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium text-ink-muted transition-colors hover:bg-surface-2 hover:text-ink"
              active-class="bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300"
            >
              <span class="flex h-5 w-5 shrink-0 items-center justify-center" aria-hidden="true">
                <svg
                  v-if="item.icon === 'dashboard'"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  class="h-5 w-5"
                >
                  <path d="M3 12 12 4l9 8" />
                  <path d="M5 10v10h14V10" />
                </svg>
                <svg
                  v-else-if="item.icon === 'patients'"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  class="h-5 w-5"
                >
                  <circle cx="9" cy="8" r="3.5" />
                  <path d="M2 20c0-3.3 3.1-6 7-6s7 2.7 7 6" />
                  <circle cx="17" cy="6" r="2.5" />
                  <path d="M22 16c0-2-1.6-3.6-3.5-3.6" />
                </svg>
                <svg
                  v-else-if="item.icon === 'calendar'"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  class="h-5 w-5"
                >
                  <rect x="3" y="5" width="18" height="16" rx="2" />
                  <path d="M3 9h18" />
                  <path d="M8 3v4M16 3v4" />
                </svg>
                <svg
                  v-else-if="item.icon === 'encounters'"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  class="h-5 w-5"
                >
                  <path d="M5 4h11l4 4v12H5z" />
                  <path d="M9 12h6M9 16h6M9 8h3" />
                </svg>
                <svg
                  v-else-if="item.icon === 'settings'"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  class="h-5 w-5"
                >
                  <circle cx="12" cy="12" r="3" />
                  <path
                    d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h.1a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"
                  />
                </svg>
              </span>
              <span v-if="!ui.sidebarCollapsed" class="truncate">{{ item.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </nav>

      <div class="border-t border-line p-2 text-xs text-ink-muted">
        <span v-if="!ui.sidebarCollapsed">v0.0.0 · Vue Edition</span>
      </div>
    </aside>

    <!-- Main column -->
    <div class="flex min-w-0 flex-1 flex-col">
      <!-- Topbar -->
      <header
        class="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4"
      >
        <h1 class="text-sm font-semibold tracking-tight">
          OpenEMR <span class="text-ink-muted">· Vue Edition</span>
        </h1>

        <form
          class="ml-2 hidden flex-1 max-w-md md:block"
          role="search"
          @submit.prevent="onSearchSubmit"
        >
          <label class="sr-only" for="topbar-search">Search</label>
          <div class="relative">
            <span
              class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-ink-muted"
              aria-hidden="true"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>
            </span>
            <input
              id="topbar-search"
              v-model="search"
              type="search"
              placeholder="Search patients, encounters…"
              class="w-full rounded-lg border border-line bg-surface-2 py-1.5 pl-9 pr-3 text-sm placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
            />
          </div>
        </form>

        <div class="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            class="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2"
            @click="ui.toggleAgentDrawer"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
              <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
              <circle cx="12" cy="12" r="4" />
            </svg>
            <span class="hidden sm:inline">AgentForge</span>
          </button>

          <button
            type="button"
            class="rounded-lg p-2 text-ink-muted hover:bg-surface-2 hover:text-ink"
            :aria-label="themeLabel"
            @click="ui.toggleTheme"
          >
            <svg
              v-if="themeIcon === 'sun'"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              class="h-5 w-5"
            >
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
            </svg>
            <svg
              v-else
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              class="h-5 w-5"
            >
              <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
            </svg>
          </button>

          <div ref="userMenuRef" class="relative">
            <button
              type="button"
              class="flex items-center gap-2 rounded-lg p-1 hover:bg-surface-2"
              aria-haspopup="menu"
              :aria-expanded="userMenuOpen"
              @click="userMenuOpen = !userMenuOpen"
            >
              <span
                class="flex h-8 w-8 items-center justify-center rounded-full bg-primary-100 text-sm font-semibold text-primary-700"
                aria-hidden="true"
              >
                {{ (auth.user?.name ?? '?').charAt(0) }}
              </span>
              <span class="hidden text-sm font-medium md:inline">
                {{ auth.user?.name ?? 'Guest' }}
              </span>
            </button>
            <div
              v-if="userMenuOpen"
              role="menu"
              class="absolute right-0 z-30 mt-2 w-48 rounded-lg border border-line bg-surface p-1 shadow-card-lg"
            >
              <button
                type="button"
                class="block w-full rounded-md px-3 py-1.5 text-left text-sm hover:bg-surface-2"
                @click="
                  () => {
                    userMenuOpen = false
                    router.push({ name: 'settings' })
                  }
                "
              >
                Settings
              </button>
              <button
                type="button"
                class="block w-full rounded-md px-3 py-1.5 text-left text-sm text-danger-600 hover:bg-danger-50 dark:hover:bg-danger-700/20"
                @click="onLogout"
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </header>

      <!-- Routed content -->
      <main class="min-h-0 flex-1 overflow-y-auto p-6">
        <router-view />
      </main>
    </div>

    <!-- Drawer mount point lives in index.html so any view can teleport here. -->
  </div>
</template>
