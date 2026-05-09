<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { citationKey, type Citation } from '@/composables/useAgentTurn'
import { useAgentForgeStore } from '@/stores/agentforge'

interface Props {
  highlightedId: string | null
}

const props = defineProps<Props>()

const store = useAgentForgeStore()

interface CitationGroup {
  // Visual group label, e.g. "Chart" / "Guideline" / "Lab PDF" /
  // "Intake".
  readonly groupLabel: string
  readonly entries: readonly { readonly key: string; readonly citation: Citation }[]
}

const allCitations = computed<readonly Citation[]>(() => {
  const out: Citation[] = []
  const seen = new Set<string>()
  for (const m of store.messages) {
    for (const c of m.citations ?? []) {
      const key = citationKey(c)
      if (seen.has(key)) continue
      seen.add(key)
      out.push(c)
    }
  }
  return out
})

function sourceTypeLabel(sourceType: Citation['source_type']): string {
  switch (sourceType) {
    case 'openemr_record':
      return 'Chart'
    case 'guideline':
      return 'Guideline'
    case 'lab_pdf':
      return 'Lab PDF'
    case 'intake_form':
      return 'Intake'
  }
}

const grouped = computed<readonly CitationGroup[]>(() => {
  const map = new Map<string, { readonly key: string; readonly citation: Citation }[]>()
  for (const c of allCitations.value) {
    const groupLabel = sourceTypeLabel(c.source_type)
    const list = map.get(groupLabel) ?? []
    list.push({ key: citationKey(c), citation: c })
    map.set(groupLabel, list)
  }
  return [...map.entries()].map(([groupLabel, entries]) => ({ groupLabel, entries }))
})

const cardRefs = ref<Map<string, HTMLElement>>(new Map())
const expanded = ref<Set<string>>(new Set<string>())

function setCardRef(id: string, el: unknown): void {
  if (el === null || el === undefined) {
    cardRefs.value.delete(id)
    return
  }
  if (el instanceof HTMLElement) {
    cardRefs.value.set(id, el)
  }
}

function toggleExpanded(id: string): void {
  const next = new Set(expanded.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  expanded.value = next
}

function isExpanded(id: string): boolean {
  return expanded.value.has(id)
}

function badgeClass(sourceType: Citation['source_type']): string {
  switch (sourceType) {
    case 'openemr_record':
      return 'bg-info-100 text-info-700 dark:bg-info-700/20 dark:text-info-500'
    case 'guideline':
      return 'bg-primary-100 text-primary-700 dark:bg-primary-700/20 dark:text-primary-300'
    case 'lab_pdf':
      return 'bg-warning-100 text-warning-700 dark:bg-warning-700/20 dark:text-warning-500'
    case 'intake_form':
      return 'bg-success-100 text-success-700 dark:bg-success-700/20 dark:text-success-500'
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
      <section v-for="g in grouped" :key="g.groupLabel" class="flex flex-col gap-2">
        <header class="flex items-baseline justify-between gap-2">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            {{ g.groupLabel }}
          </h3>
          <span class="text-[11px] text-ink-muted">{{ g.entries.length }}</span>
        </header>

        <article
          v-for="entry in g.entries"
          :key="entry.key"
          :ref="(el) => setCardRef(entry.key, el)"
          class="rounded-xl border border-line bg-surface p-3 shadow-card transition-colors"
          :class="
            highlightedId === entry.key
              ? 'border-primary-500 ring-2 ring-primary-500/30'
              : ''
          "
        >
          <div class="flex items-center gap-2">
            <span
              class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide"
              :class="badgeClass(entry.citation.source_type)"
            >
              {{ entry.citation.source_id }}
            </span>
            <span
              v-if="entry.citation.page_or_section !== null"
              class="text-[11px] text-ink-muted"
            >
              {{ entry.citation.page_or_section }}
            </span>
          </div>
          <p
            v-if="entry.citation.quote_or_value !== null"
            class="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink"
            :class="isExpanded(entry.key) ? '' : 'line-clamp-3'"
          >{{ entry.citation.quote_or_value }}</p>
          <p
            v-else
            class="mt-2 text-sm italic text-ink-muted"
          >No quote available.</p>
          <div
            v-if="entry.citation.field_or_chunk_id !== null"
            class="mt-2 truncate text-[10px] font-mono text-ink-muted"
            :title="entry.citation.field_or_chunk_id"
          >{{ entry.citation.field_or_chunk_id }}</div>
          <div class="mt-3 flex justify-end">
            <button
              v-if="entry.citation.quote_or_value !== null"
              type="button"
              class="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-xs font-medium text-ink-muted hover:bg-surface-2 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
              :title="isExpanded(entry.key) ? 'Collapse' : 'View full source'"
              :aria-expanded="isExpanded(entry.key)"
              @click="toggleExpanded(entry.key)"
            >
              <svg
                v-if="!isExpanded(entry.key)"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                class="h-3 w-3"
                aria-hidden="true"
              >
                <path d="M14 4h6v6" />
                <path d="M10 14 20 4" />
                <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
              </svg>
              <svg
                v-else
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                class="h-3 w-3"
                aria-hidden="true"
              >
                <path d="m18 15-6-6-6 6" />
              </svg>
              {{ isExpanded(entry.key) ? 'Collapse' : 'View source' }}
            </button>
          </div>
        </article>
      </section>
    </div>
  </div>
</template>
