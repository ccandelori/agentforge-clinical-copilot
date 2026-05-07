<script setup lang="ts">
// Root component. AppShell is applied per-route via nested routing.
import AgentDrawer from '@/components/agentforge/AgentDrawer.vue'
</script>

<template>
  <router-view />
  <AgentDrawer />
</template>
