<script setup lang="ts">
interface Props {
  screenName: string
  waveAgent?: string
}

withDefaults(defineProps<Props>(), {
  waveAgent: 'Wave 2',
})
</script>

<template>
  <div class="flex min-h-full items-center justify-center">
    <section
      class="card max-w-md p-8 text-center"
      role="status"
      aria-live="polite"
    >
      <div
        class="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary-100 text-primary-700 dark:bg-primary-900/40 dark:text-primary-300"
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-6 w-6">
          <path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </div>
      <h1 class="text-base font-semibold">
        {{ screenName }}
      </h1>
      <p class="mt-1 text-sm text-ink-muted">
        coming from {{ waveAgent }} agent
      </p>
    </section>
  </div>
</template>
