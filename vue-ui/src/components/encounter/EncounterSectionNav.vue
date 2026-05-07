<script setup lang="ts">
interface NavItem {
  readonly id: string
  readonly label: string
}

interface Props {
  items: readonly NavItem[]
  activeId: string
}

defineProps<Props>()

defineEmits<{
  (e: 'select', id: string): void
}>()
</script>

<template>
  <nav
    aria-label="Encounter sections"
    class="sticky top-32 flex flex-col gap-0.5 rounded-xl border border-line bg-surface p-2 shadow-card"
  >
    <button
      v-for="item in items"
      :key="item.id"
      type="button"
      class="flex items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition-colors"
      :class="
        activeId === item.id
          ? 'bg-primary-50 text-primary-700 font-medium dark:bg-primary-900/30 dark:text-primary-300'
          : 'text-ink-muted hover:bg-surface-2 hover:text-ink'
      "
      @click="$emit('select', item.id)"
    >
      <span>{{ item.label }}</span>
      <span
        v-if="activeId === item.id"
        class="h-1.5 w-1.5 rounded-full bg-primary-600"
        aria-hidden="true"
      />
    </button>
  </nav>
</template>
