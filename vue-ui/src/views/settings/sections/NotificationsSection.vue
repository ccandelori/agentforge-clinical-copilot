<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue'
import { usePreferencesStore, type NotificationPrefs } from '@/stores/preferences'

const prefs = usePreferencesStore()

interface Toggle {
  readonly key: keyof NotificationPrefs
  readonly title: string
  readonly description: string
}

const TOGGLES: readonly Toggle[] = [
  {
    key: 'browser',
    title: 'Browser notifications',
    description: 'Get desktop alerts for new messages and orders.',
  },
  {
    key: 'email',
    title: 'Email digest',
    description: 'A daily summary of unread items, sent at 7am.',
  },
  {
    key: 'afterHours',
    title: 'After-hours pager',
    description: 'Route urgent results to your pager outside business hours.',
  },
]

function toggle(key: keyof NotificationPrefs): void {
  prefs.setNotification(key, !prefs.notifications[key])
}
</script>

<template>
  <BaseCard title="Notifications">
    <ul class="divide-y divide-line">
      <li
        v-for="t in TOGGLES"
        :key="t.key"
        class="flex items-start justify-between gap-4 py-4 first:pt-0 last:pb-0"
      >
        <div class="min-w-0">
          <p class="text-sm font-medium text-ink">{{ t.title }}</p>
          <p class="mt-0.5 text-xs text-ink-muted">{{ t.description }}</p>
        </div>
        <button
          type="button"
          role="switch"
          :aria-checked="prefs.notifications[t.key]"
          :aria-label="t.title"
          class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
          :class="prefs.notifications[t.key] ? 'bg-primary-600' : 'bg-neutral-300 dark:bg-neutral-700'"
          @click="toggle(t.key)"
        >
          <span
            class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform"
            :class="prefs.notifications[t.key] ? 'translate-x-5' : 'translate-x-0.5'"
          />
        </button>
      </li>
    </ul>
  </BaseCard>
</template>
