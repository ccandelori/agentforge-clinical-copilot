<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import type { Citation, CitationKind } from '@/composables/useAgentTurn'
import { useAgentDrawer } from '@/stores/agentDrawer'

// CitationsPane — lists every citation surfaced in the current scope's
// conversation, grouped by source/date with a kind badge and a stub
// "View source" link. When `highlightedId` changes, the matching card
// scrolls into view and gets a 1.5s pulse highlight.
//
// Lifted from `vue-ui/src/components/agentforge/CitationsPane.vue`,
// rewired against the dashboard's `useAgentDrawer` store
// (`currentCitations`) and translated from Tailwind to Bootstrap 5 +
// design tokens.

interface Props {
  highlightedId: string | null
}

const props = defineProps<Props>()

const store = useAgentDrawer()

interface CitationGroup {
  readonly key: string
  readonly source: string
  readonly date: string
  readonly citations: readonly Citation[]
}

const grouped = computed<readonly CitationGroup[]>(() => {
  const map = new Map<string, Citation[]>()
  for (const c of store.currentCitations) {
    const key = `${c.source}|${c.date}`
    const list = map.get(key) ?? []
    list.push(c)
    map.set(key, list)
  }
  const out: CitationGroup[] = []
  for (const [key, list] of map.entries()) {
    const first = list[0]
    if (first === undefined) continue
    out.push({
      key,
      source: first.source,
      date: first.date,
      citations: list,
    })
  }
  return out
})

function kindLabel(kind: CitationKind): string {
  switch (kind) {
    case 'note':
      return 'Note'
    case 'lab':
      return 'Lab'
    case 'med':
      return 'Med'
    case 'problem':
      return 'Problem'
    case 'allergy':
      return 'Allergy'
  }
}

function kindBadgeClass(kind: CitationKind): string {
  switch (kind) {
    case 'note':
      return 'text-bg-info'
    case 'lab':
      return 'text-bg-warning'
    case 'med':
      return 'text-bg-success'
    case 'problem':
      return 'text-bg-danger'
    case 'allergy':
      return 'text-bg-secondary'
  }
}

function kindIcon(kind: CitationKind): string {
  switch (kind) {
    case 'note':
      return 'bi-file-earmark-text'
    case 'lab':
      return 'bi-clipboard2-pulse'
    case 'med':
      return 'bi-capsule'
    case 'problem':
      return 'bi-exclamation-triangle'
    case 'allergy':
      return 'bi-shield-exclamation'
  }
}

const cardRefs = ref<Map<string, HTMLElement>>(new Map())

function setCardRef(id: string, el: unknown): void {
  if (el === null || el === undefined) {
    cardRefs.value.delete(id)
    return
  }
  if (el instanceof HTMLElement) {
    cardRefs.value.set(id, el)
  }
}

watch(
  () => props.highlightedId,
  (id) => {
    if (id === null) return
    void nextTick(() => {
      const el = cardRefs.value.get(id)
      if (el !== undefined) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    })
  },
  { immediate: true },
)
</script>

<template>
  <div
    class="agent-citations-pane h-100 overflow-auto px-3 py-3"
    data-test="agent-citations-pane"
  >
    <div
      v-if="grouped.length === 0"
      class="d-flex flex-column align-items-center justify-content-center text-center h-100 gap-2"
      data-test="agent-citations-empty"
    >
      <div
        class="agent-citations-pane__empty-icon d-flex align-items-center justify-content-center rounded-circle"
        aria-hidden="true"
      >
        <i class="bi bi-file-earmark-text"></i>
      </div>
      <h3 class="h6 mb-0">No citations yet</h3>
      <p class="small mb-0 text-body-secondary" style="max-width: 22rem;">
        Ask a clinical question — AgentForge will pull supporting citations
        from the chart and list them here.
      </p>
    </div>

    <div v-else class="d-flex flex-column gap-4">
      <section
        v-for="g in grouped"
        :key="g.key"
        class="d-flex flex-column gap-2"
      >
        <header class="d-flex justify-content-between align-items-baseline gap-2">
          <h3 class="text-uppercase small fw-semibold text-body-secondary mb-0">
            {{ g.source }}
          </h3>
          <span class="text-body-secondary" style="font-size: 0.7rem;">
            {{ g.date }}
          </span>
        </header>

        <article
          v-for="c in g.citations"
          :key="c.id"
          :ref="(el) => setCardRef(c.id, el)"
          class="agent-citation-card border rounded p-3"
          :class="
            highlightedId === c.id ? 'agent-citation-card--highlighted' : ''
          "
          data-test="agent-citation-card"
        >
          <div class="d-flex align-items-center gap-2">
            <span
              class="badge d-inline-flex align-items-center gap-1"
              :class="kindBadgeClass(c.kind)"
            >
              <i :class="['bi', kindIcon(c.kind)]" aria-hidden="true"></i>
              {{ kindLabel(c.kind) }}
            </span>
            <span class="text-body-secondary" style="font-size: 0.7rem;">
              {{ c.date }}
            </span>
          </div>
          <p class="mt-2 mb-0 small">{{ c.excerpt }}</p>
          <p
            v-if="c.provenance !== undefined"
            class="mb-0 mt-1 text-body-secondary"
            style="font-size: 0.7rem;"
          >
            {{ c.provenance }}
          </p>
          <div class="mt-3 d-flex justify-content-end">
            <button
              type="button"
              class="btn btn-outline-secondary btn-sm d-inline-flex align-items-center gap-1"
              title="View source (coming soon)"
              data-test="agent-citation-view-source"
              disabled
            >
              <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>
              View source
            </button>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>

<style scoped>
.agent-citations-pane__empty-icon {
  width: 3rem;
  height: 3rem;
  background-color: var(--surface-2);
  color: var(--ink-muted);
  font-size: 1.25rem;
}

.agent-citation-card {
  background-color: var(--surface);
  border-color: var(--line) !important;
  transition: border-color 150ms ease, box-shadow 150ms ease;
}

.agent-citation-card--highlighted {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px rgb(var(--accent-rgb) / 0.25);
}
</style>
