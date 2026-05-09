<script setup lang="ts">
import { computed } from 'vue'

import type { CareTeam, CareTeamMember } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseEmptyState from '@/components/ui/BaseEmptyState.vue'

/**
 * Care Team card.
 *
 * Lists the patient's active care team(s) and their members. Backed by
 * `GET /api/fhir/CareTeam?patient={uuid}&status=active` (see
 * `getCareTeams()` in `@/api/mock`). Synthea-imported demo personas
 * have empty `care_teams` / `care_team_member` tables out of the box;
 * `scripts/seed/care_team.sql` populates them for the four demo
 * personas (Chen / Whitaker / Reyes / Kowalski).
 */

interface Props {
  readonly careTeams: readonly CareTeam[]
  readonly loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
})

const totalMembers = computed<number>(() =>
  props.careTeams.reduce((acc, t) => acc + t.members.length, 0),
)

/**
 * Pull initials off a member display name. Falls back to '?' when the
 * upstream FHIR Reference.display is blank — which happens occasionally
 * when the underlying `users` row never had `fname`/`lname` populated.
 */
function initialsOf(member: CareTeamMember): string {
  const parts = member.name.trim().split(/\s+/).filter((p) => p.length > 0)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}
</script>

<template>
  <BaseCard :padded="false">
    <template #title>
      <div class="flex items-center gap-2">
        <h2 class="text-sm font-semibold tracking-tight">Care Team</h2>
        <BaseBadge v-if="totalMembers > 0" variant="info">
          {{ totalMembers }}
        </BaseBadge>
      </div>
    </template>

    <div v-if="loading" class="space-y-2 p-4">
      <div
        v-for="n in 2"
        :key="n"
        class="flex animate-pulse items-center gap-3 rounded-md border border-line p-3"
      >
        <div class="h-8 w-8 rounded-full bg-surface-2" />
        <div class="flex-1 space-y-1.5">
          <div class="h-3 w-1/3 rounded bg-surface-2" />
          <div class="h-3 w-1/4 rounded bg-surface-2" />
        </div>
      </div>
    </div>

    <BaseEmptyState
      v-else-if="careTeams.length === 0 || totalMembers === 0"
      icon="✦"
      title="No care team on file"
      message="Add care team members from the patient's chart actions."
    />

    <div v-else class="divide-y divide-line">
      <section v-for="team in careTeams" :key="team.id" class="px-5 py-3">
        <header v-if="team.name" class="mb-2 flex items-center gap-2">
          <h3 class="text-xs font-medium uppercase tracking-wide text-ink-muted">
            {{ team.name }}
          </h3>
        </header>
        <ul class="space-y-2">
          <li
            v-for="member in team.members"
            :key="member.id"
            class="flex items-center gap-3"
          >
            <span
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11px] font-semibold text-ink-muted"
              aria-hidden="true"
            >
              {{ initialsOf(member) }}
            </span>
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium">
                {{ member.name || 'Unknown member' }}
              </p>
            </div>
            <BaseBadge v-if="member.role" variant="neutral">
              {{ member.role }}
            </BaseBadge>
          </li>
        </ul>
      </section>
    </div>
  </BaseCard>
</template>
