<script setup lang="ts">
import { computed, ref } from 'vue'

import type { User } from '@/api/mock'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { useAuthStore } from '@/stores/auth'

const FALLBACK_USER: User = {
  id: 'u-fallback',
  username: 'guest',
  fullName: 'Guest User',
  role: 'staff',
}

const auth = useAuthStore()
const currentUser = computed<User>(() => auth.user ?? FALLBACK_USER)

const displayName = ref<string>(currentUser.value.fullName)
const email = ref<string>(`${currentUser.value.username}@openemr.local`)
const phone = ref<string>('')

const saved = ref<boolean>(false)
let timer: number | undefined

function save(): void {
  // Mock-only: no real backend.
  saved.value = true
  if (timer !== undefined) window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    saved.value = false
  }, 2400)
}

const roleVariant = computed<'info' | 'success' | 'warning' | 'neutral'>(() => {
  switch (currentUser.value.role) {
    case 'admin':
      return 'warning'
    case 'physician':
      return 'info'
    case 'nurse':
      return 'success'
    case 'staff':
      return 'neutral'
  }
})
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
        <span class="text-sm font-medium text-ink">Role</span>
        <div class="flex items-center gap-2">
          <BaseBadge :variant="roleVariant">
            {{ currentUser.role }}
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
            Changes are local to this session.
          </p>
        </transition>
        <BaseButton variant="primary" size="sm" @click="save">
          Save
        </BaseButton>
      </div>
    </template>
  </BaseCard>
</template>
