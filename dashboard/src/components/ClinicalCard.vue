<script setup lang="ts">
import { computed, ref } from 'vue'

// Layout wrapper used by every chart card (T38.4–T38.9). Owns the
// loading/empty/error chrome so per-card setup blocks stay focused on
// data shaping. Default slot renders when state === 'ready' (or unset).
//
// Collapse/expand is opt-in via `collapsible`. The header becomes a
// button that toggles body visibility. `defaultCollapsed` controls the
// initial state. Header-actions stay outside the toggle button so a
// per-card refresh / kebab control doesn't fold the card.

type CardState = 'loading' | 'empty' | 'error' | 'ready'

const props = defineProps<{
  title: string
  count?: number | null
  state?: CardState
  error?: Error | null
  collapsible?: boolean
  defaultCollapsed?: boolean
}>()

const collapsed = ref<boolean>(props.defaultCollapsed ?? false)

function toggle(): void {
  if (props.collapsible === true) {
    collapsed.value = !collapsed.value
  }
}

const resolvedState = computed<CardState>(() => props.state ?? 'ready')
const showCount = computed<boolean>(
  () => props.count !== null && props.count !== undefined,
)
const bodyVisible = computed<boolean>(
  () => props.collapsible !== true || !collapsed.value,
)
</script>

<template>
  <section class="card mb-3">
    <header
      class="card-header bg-body-tertiary d-flex justify-content-between align-items-center"
    >
      <button
        v-if="collapsible"
        type="button"
        class="btn btn-link p-0 text-start text-decoration-none text-body flex-grow-1 d-flex align-items-center"
        :aria-expanded="!collapsed"
        @click="toggle"
      >
        <i
          class="bi me-2"
          :class="collapsed ? 'bi-chevron-right' : 'bi-chevron-down'"
          aria-hidden="true"
        ></i>
        <strong>{{ title }}</strong>
        <span v-if="showCount" class="text-muted ms-2 small">({{ count }})</span>
      </button>
      <div v-else>
        <strong>{{ title }}</strong>
        <span v-if="showCount" class="text-muted ms-2 small">({{ count }})</span>
      </div>
      <div>
        <slot name="header-actions" />
      </div>
    </header>
    <div v-show="bodyVisible" class="card-body">
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
