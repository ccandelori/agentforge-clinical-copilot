<script setup lang="ts">
interface Props {
  title?: string
  padded?: boolean
}

withDefaults(defineProps<Props>(), {
  title: '',
  padded: true,
})
</script>

<template>
  <section class="card overflow-hidden">
    <header
      v-if="title || $slots.title || $slots.actions"
      class="flex items-center justify-between gap-3 border-b border-line px-5 py-3"
    >
      <div class="min-w-0">
        <slot name="title">
          <h2 class="truncate text-sm font-semibold tracking-tight">{{ title }}</h2>
        </slot>
      </div>
      <div v-if="$slots.actions" class="flex shrink-0 items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div :class="padded ? 'p-5' : ''">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="border-t border-line bg-surface-2 px-5 py-3">
      <slot name="footer" />
    </footer>
  </section>
</template>
