<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'

interface Binding {
  readonly action: string
  readonly keys: readonly string[]
  readonly description: string
}

const BINDINGS: readonly Binding[] = [
  { action: 'Open command palette', keys: ['⌘', 'K'], description: 'Search anything in the app' },
  { action: 'Search patients', keys: ['⌘', 'P'], description: 'Jump to a patient by name or MRN' },
  { action: 'Go to dashboard', keys: ['G', 'D'], description: 'Chord shortcut' },
  { action: 'Go to calendar', keys: ['G', 'C'], description: 'Chord shortcut' },
  { action: 'Go to patients', keys: ['G', 'P'], description: 'Chord shortcut' },
  { action: 'Toggle AgentForge drawer', keys: ['⌘', '.'], description: 'Open the Co-Pilot side panel' },
  { action: 'Toggle theme', keys: ['⌘', 'Shift', 'L'], description: 'Cycle light / dark / system' },
  { action: 'Toggle sidebar', keys: ['⌘', '\\'], description: 'Collapse or expand the rail' },
]
</script>

<template>
  <BaseCard title="Keybindings">
    <div class="flex flex-col gap-4">
      <p class="text-xs text-ink-muted">
        Built-in keyboard shortcuts. Customization is on the roadmap.
      </p>
      <div class="overflow-hidden rounded-lg border border-line">
        <table class="w-full text-sm">
          <thead class="bg-surface-2 text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th class="px-4 py-2 font-medium">Action</th>
              <th class="px-4 py-2 font-medium">Shortcut</th>
              <th class="hidden px-4 py-2 font-medium md:table-cell">Notes</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="b in BINDINGS" :key="b.action" class="bg-surface">
              <td class="px-4 py-2.5 font-medium text-ink">{{ b.action }}</td>
              <td class="px-4 py-2.5">
                <span class="inline-flex items-center gap-1">
                  <template v-for="(k, i) in b.keys" :key="`${b.action}-${i}`">
                    <kbd
                      class="inline-flex min-w-[1.5rem] items-center justify-center rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-ink shadow-sm"
                    >
                      {{ k }}
                    </kbd>
                    <span
                      v-if="i < b.keys.length - 1"
                      class="text-xs text-ink-muted"
                      aria-hidden="true"
                    >
                      then
                    </span>
                  </template>
                </span>
              </td>
              <td class="hidden px-4 py-2.5 text-xs text-ink-muted md:table-cell">
                {{ b.description }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-between">
        <p class="text-xs text-ink-muted">Customize bindings — coming soon.</p>
        <BaseButton variant="secondary" size="sm" disabled>
          Customize
        </BaseButton>
      </div>
    </div>
  </BaseCard>
</template>
