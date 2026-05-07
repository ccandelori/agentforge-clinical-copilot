<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { usePreferencesStore } from '@/stores/preferences'

import AboutSection from './sections/AboutSection.vue'
import AppearanceSection from './sections/AppearanceSection.vue'
import DensitySection from './sections/DensitySection.vue'
import KeybindingsSection from './sections/KeybindingsSection.vue'
import NotificationsSection from './sections/NotificationsSection.vue'
import ProfileSection from './sections/ProfileSection.vue'

interface SectionDef {
  readonly id: string
  readonly label: string
}

const SECTIONS: readonly SectionDef[] = [
  { id: 'profile', label: 'Profile' },
  { id: 'appearance', label: 'Appearance' },
  { id: 'density', label: 'Density' },
  { id: 'keybindings', label: 'Keybindings' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'about', label: 'About' },
]

// Ensure preferences are reflected to the DOM. Idempotent — safe even if
// the app shell already called hydrate at boot.
const prefs = usePreferencesStore()
prefs.hydrate()

const activeId = ref<string>(SECTIONS[0]?.id ?? 'profile')
let observer: IntersectionObserver | undefined

function jumpTo(id: string): void {
  if (typeof document === 'undefined') return
  const el = document.getElementById(id)
  if (el === null) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeId.value = id
}

function findScrollableAncestor(el: HTMLElement | null): HTMLElement | null {
  let node: HTMLElement | null = el
  while (node !== null) {
    const style = window.getComputedStyle(node)
    if (
      (style.overflowY === 'auto' || style.overflowY === 'scroll')
      && node.scrollHeight > node.clientHeight
    ) {
      return node
    }
    node = node.parentElement
  }
  return null
}

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') return

  // The AppShell's <main> element is the scroll container in production,
  // but we keep this resilient by walking up to find any scrollable ancestor.
  const firstSection = document.getElementById(SECTIONS[0]?.id ?? 'profile')
  const root = findScrollableAncestor(firstSection)

  observer = new IntersectionObserver(
    (entries) => {
      // Pick the entry closest to the top of the viewport that is intersecting.
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
      const first = visible[0]
      if (first !== undefined) {
        activeId.value = first.target.id
      }
    },
    {
      root,
      // Trigger when a section enters the upper portion of the viewport.
      rootMargin: '0px 0px -60% 0px',
      threshold: [0, 0.1, 0.5, 1],
    },
  )

  for (const section of SECTIONS) {
    const el = document.getElementById(section.id)
    if (el !== null) observer.observe(el)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
})
</script>

<template>
  <div class="flex gap-6">
    <!-- Left rail -->
    <aside
      class="hidden w-56 shrink-0 lg:block"
      aria-label="Settings sections"
    >
      <div class="sticky top-6">
        <h1 class="px-3 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Settings
        </h1>
        <nav class="mt-3 flex flex-col gap-0.5">
          <button
            v-for="s in SECTIONS"
            :key="s.id"
            type="button"
            class="flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
            :class="activeId === s.id
              ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300'
              : 'text-ink-muted hover:bg-surface-2 hover:text-ink'"
            :aria-current="activeId === s.id ? 'true' : undefined"
            @click="jumpTo(s.id)"
          >
            <span
              class="inline-block h-1.5 w-1.5 rounded-full transition-colors"
              :class="activeId === s.id ? 'bg-primary-600' : 'bg-transparent'"
              aria-hidden="true"
            />
            {{ s.label }}
          </button>
        </nav>
      </div>
    </aside>

    <!-- Right column: sections scroll inside the AppShell's <main>. -->
    <div class="min-w-0 flex-1">
      <header class="mb-6 lg:hidden">
        <h1 class="text-2xl font-semibold tracking-tight text-ink">Settings</h1>
        <p class="mt-1 text-sm text-ink-muted">
          Manage your profile, appearance, and notifications.
        </p>
      </header>

      <!-- Mobile/condensed nav -->
      <nav
        class="-mx-1 mb-6 flex gap-1 overflow-x-auto pb-2 lg:hidden"
        aria-label="Settings sections"
      >
        <button
          v-for="s in SECTIONS"
          :key="`m-${s.id}`"
          type="button"
          class="shrink-0 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
          :class="activeId === s.id
            ? 'border-primary-500 bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300'
            : 'border-line bg-surface text-ink-muted hover:text-ink'"
          @click="jumpTo(s.id)"
        >
          {{ s.label }}
        </button>
      </nav>

      <div class="flex flex-col gap-6 pb-12">
        <section id="profile" class="scroll-mt-6">
          <ProfileSection />
        </section>
        <section id="appearance" class="scroll-mt-6">
          <AppearanceSection />
        </section>
        <section id="density" class="scroll-mt-6">
          <DensitySection />
        </section>
        <section id="keybindings" class="scroll-mt-6">
          <KeybindingsSection />
        </section>
        <section id="notifications" class="scroll-mt-6">
          <NotificationsSection />
        </section>
        <section id="about" class="scroll-mt-6">
          <AboutSection />
        </section>
      </div>
    </div>
  </div>
</template>
