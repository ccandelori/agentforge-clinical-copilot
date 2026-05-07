<script setup lang="ts">
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'
import type { PendingOrder } from '@/composables/useEncounterDraft'

interface Props {
  orders: readonly PendingOrder[]
  disabled?: boolean
}

withDefaults(defineProps<Props>(), { disabled: false })

defineEmits<{
  (e: 'remove', id: string): void
}>()
</script>

<template>
  <BaseCard title="Orders">
    <div v-if="orders.length === 0">
      <BaseEmptyState
        title="No pending orders"
        message="Lab and imaging orders queued during this visit will appear here."
      />
    </div>
    <ul v-else class="divide-y divide-line">
      <li
        v-for="o in orders"
        :key="o.id"
        class="flex items-center justify-between gap-3 py-2.5"
      >
        <div class="min-w-0">
          <p class="text-sm font-medium text-ink">{{ o.label }}</p>
          <p class="text-xs text-ink-muted">{{ o.detail }}</p>
        </div>
        <button
          type="button"
          class="text-xs text-danger-600 hover:underline disabled:opacity-50"
          :disabled="disabled"
          @click="$emit('remove', o.id)"
        >
          Remove
        </button>
      </li>
    </ul>
  </BaseCard>
</template>
