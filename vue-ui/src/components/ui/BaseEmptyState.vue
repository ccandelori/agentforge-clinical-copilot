<script setup lang="ts">
interface Props {
  title: string
  message?: string
  icon?: string
}

withDefaults(defineProps<Props>(), {
  message: '',
  icon: '',
})
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-3 py-10 text-center">
    <div
      v-if="icon || $slots.icon"
      class="flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-ink-muted"
      aria-hidden="true"
    >
      <slot name="icon">
        <span class="text-xl">{{ icon }}</span>
      </slot>
    </div>
    <h3 class="text-sm font-semibold text-ink">{{ title }}</h3>
    <p v-if="message" class="max-w-sm text-sm text-ink-muted">{{ message }}</p>
    <div v-if="$slots.action" class="mt-2">
      <slot name="action" />
    </div>
  </div>
</template>
