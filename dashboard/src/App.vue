<script setup lang="ts">
import { watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'

import AgentDrawer from '@/components/AgentDrawer.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
import { useAgentDrawer } from '@/stores/agentDrawer'

// AgentForge drawer (T38.10) is mounted at the App root so the
// drawer + its conversations survive route changes. The watcher
// below mirrors the active /patient/:pid into the drawer store —
// the store's own conflict policy decides whether the change is
// applied immediately or staged for the user.

const route = useRoute()
const drawer = useAgentDrawer()

watch(
  () => route.params.pid,
  (pid) => {
    if (typeof pid === 'string' && pid !== '') {
      drawer.setActivePatient(pid)
      return
    }
    drawer.setActivePatient(null)
  },
  { immediate: true },
)
</script>

<template>
  <RouterView />
  <div class="theme-toggle-anchor">
    <ThemeToggle />
  </div>
  <AgentDrawer />
</template>

<style scoped>
/*
 * Theme toggle is anchored top-right of the viewport so it stays
 * available across every route (login, picker, patient chart) without
 * having to edit each view's own header. z-index sits above page chrome
 * but below the agent drawer's overlay.
 */
.theme-toggle-anchor {
  position: fixed;
  top: 0.75rem;
  right: 0.75rem;
  z-index: 1040;
}
</style>
