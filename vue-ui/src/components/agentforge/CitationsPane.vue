<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { useAgentForgeStore, type Citation } from '@/stores/agentforge'

interface Props {
  highlightedId: string | null
}

const props = defineProps<Props>()

const store = useAgentForgeStore()

interface CitationGroup {
  readonly source: string
  readonly date: string
  readonly citations: readonly Citation[]
}

const allCitations = computed<readonly Citation[]>(() => {
  const out: Citation[] = []
  const seen = new Set<string>()
  for (const m of store.messages) {
    for (const c of m.citations ?? []) {
      if (seen.has(c.id)) continue
      seen.add(c.id)
      out.push(c)
    }
  }
  return out
})

const grouped = computed<readonly CitationGroup[]>(() => {
  const map = new Map<string, Citation[]>()
  for (const c of allCitations.value) {
    const key = `${c.source}|${c.date}`
    const list = map.get(key) ?? []
    list.push(c)
    map.set(key, list)
  }
  return [...map.entries()].map(([key, list]) => {
    const [source, date] = key.split('|') as [string, string]
    return { source, date, citations: list }
  })
})

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

function kindLabel(kind: Citation['kind']): string {
  switch (kind) {
    case 'note':
      return 'Note'
    case 'lab':
      return 'Lab'
    case 'imaging':
      return 'Imaging'
    case 'medication':
      return 'Medication'
    case 'problem':
      return 'Problem'
  }
}

function kindBadgeClass(kind: Citation['kind']): string {
  switch (kind) {
    case 'note':
      return 'bg-info-100 text-info-700 dark:bg-info-700/20 dark:text-info-500'
    case 'lab':
      return 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-500'
    case 'imaging':
      return 'bg-primary-100 text-primary-700 dark:bg-primary-700/20 dark:text-primary-300'
    case 'medication':
      return 'bg-success-100 text-success-700 dark:bg-success-700/20 dark:text-success-500'
    case 'problem':
      return 'bg-danger-100 text-danger-700 dark:bg-danger-700/20 dark:text-danger-500'
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
  <div class="h-full min-h-0 overflow-y-auto px-4 py-4">
    <div v-if="grouped.length === 0" class="flex h-full flex-col items-center justify-center gap-2 text-center">
      <div
        class="flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-ink-muted"
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-5 w-5">
          <path d="M5 4h11l4 4v12H5z" />
          <path d="M9 12h6M9 16h4M9 8h3" />
        </svg>
      </div>
      <h3 class="text-sm font-semibold text-ink">No citations yet</h3>
      <p class="max-w-sm text-xs text-ink-muted">
        Ask a clinical question — AgentForge will pull supporting citations from the chart and list them here.
      </p>
    </div>

    <div v-else class="flex flex-col gap-5">
      <section v-for="g in grouped" :key="`${g.source}-${g.date}`" class="flex flex-col gap-2">
        <header class="flex items-baseline justify-between gap-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            {{ g.source }}
          </h3>
          <span class="text-[11px] text-ink-muted">{{ g.date }}</span>
        </header>

        <article
          v-for="c in g.citations"
          :key="c.id"
          :ref="(el) => setCardRef(c.id, el)"
          class="rounded-xl border border-line bg-surface p-3 shadow-card transition-colors"
          :class="
            highlightedId === c.id
              ? 'border-primary-500 ring-2 ring-primary-500/30'
              : ''
          "
        >
          <div class="flex items-center gap-2">
            <span
              class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
              :class="kindBadgeClass(c.kind)"
            >
              {{ kindLabel(c.kind) }}
            </span>
            <span class="text-[11px] text-ink-muted">{{ c.date }}</span>
          </div>
          <p class="mt-2 text-sm leading-relaxed text-ink">{{ c.excerpt }}</p>
          <div class="mt-3 flex justify-end">
            <button
              type="button"
              class="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              title="View source (coming soon)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3 w-3" aria-hidden="true">
                <path d="M14 4h6v6" />
                <path d="M10 14 20 4" />
                <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
              </svg>
              View source
            </button>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>
