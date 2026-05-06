<script setup lang="ts">
import { computed } from 'vue'

// Layout wrapper used by every chart card (T38.4–T38.9). Owns the
// loading/empty/error chrome so per-card setup blocks stay focused on
// data shaping. Default slot renders when state === 'ready' (or unset).

type CardState = 'loading' | 'empty' | 'error' | 'ready'

const props = defineProps<{
  title: string
  count?: number | null
  state?: CardState
  error?: Error | null
}>()

const resolvedState = computed<CardState>(() => props.state ?? 'ready')
const showCount = computed<boolean>(
  () => props.count !== null && props.count !== undefined,
)
</script>

<template>
  <section class="card mb-3">
    <header
      class="card-header bg-white d-flex justify-content-between align-items-center"
    >
      <div>
        <strong>{{ title }}</strong>
        <span v-if="showCount" class="text-muted ms-2 small">({{ count }})</span>
      </div>
      <div>
        <slot name="header-actions" />
      </div>
    </header>
    <div class="card-body">
      <template v-if="resolvedState === 'loading'">
        <slot name="loading">
          <div class="d-flex align-items-center text-muted small">
            <span
              class="spinner-border spinner-border-sm me-2"
              aria-hidden="true"
            ></span>
            Loading…
          </div>
        </slot>
      </template>
      <template v-else-if="resolvedState === 'error'">
        <slot name="error" :error="error">
          <div class="text-danger small" role="alert">
            <i class="bi bi-exclamation-triangle me-1" aria-hidden="true"></i>
            {{ error?.message ?? 'Failed to load.' }}
          </div>
        </slot>
      </template>
      <template v-else-if="resolvedState === 'empty'">
        <slot name="empty">
          <div class="text-muted small">No items.</div>
        </slot>
      </template>
      <template v-else>
        <slot />
      </template>
    </div>
  </section>
</template>
