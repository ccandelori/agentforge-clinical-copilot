<script setup lang="ts">
import { computed, ref } from 'vue'

import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useAuthStore, type User } from '@/stores/auth'

const FALLBACK_USER: User = {
  sub: 'guest',
  name: 'Guest User',
  email: 'guest@openemr.local',
  fhir_user: null,
}

const auth = useAuthStore()
const currentUser = computed<User>(() => auth.user ?? FALLBACK_USER)

const displayName = ref<string>(currentUser.value.name ?? 'Guest')
const email = ref<string>(currentUser.value.email ?? '')
const phone = ref<string>('')

const saved = ref<boolean>(false)
let timer: number | undefined

function save(): void {
  // No real backend wired for profile edits — sidecar's whoami is read-only.
  saved.value = true
  if (timer !== undefined) window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    saved.value = false
  }, 2400)
}

const linkedToFhir = computed<boolean>(() => Boolean(currentUser.value.fhir_user))
</script>

<template>
  <BaseCard title="Profile">
    <div class="grid gap-5 md:grid-cols-2">
      <BaseInput
        v-model="displayName"
        label="Display name"
        autocomplete="name"
      />
      <BaseInput
        v-model="email"
        type="email"
        label="Email"
        autocomplete="email"
      />
      <BaseInput
        v-model="phone"
        type="tel"
        label="Phone"
        placeholder="555-0100"
        autocomplete="tel"
      />
      <div class="flex flex-col gap-1">
        <span class="text-sm font-medium text-ink">Identity</span>
        <div class="flex items-center gap-2">
          <BaseBadge :variant="linkedToFhir ? 'success' : 'neutral'">
            {{ linkedToFhir ? 'Linked to FHIR Practitioner' : 'Sidecar session' }}
          </BaseBadge>
          <span class="text-xs text-ink-muted">Read-only</span>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between">
        <transition
          enter-active-class="transition-opacity duration-200"
          leave-active-class="transition-opacity duration-500"
          enter-from-class="opacity-0"
          leave-to-class="opacity-0"
        >
          <p
            v-if="saved"
            class="text-xs text-success-600"
            role="status"
          >
            Profile saved.
          </p>
          <p v-else class="text-xs text-ink-muted">
            Edits are local — backed by your sidecar session.
          </p>
        </transition>
        <BaseButton variant="primary" size="sm" @click="save">
          Save
        </BaseButton>
      </div>
    </template>
  </BaseCard>
</template>
