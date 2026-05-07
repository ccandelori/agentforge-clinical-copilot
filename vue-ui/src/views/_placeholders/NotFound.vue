<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-surface-2 p-6">
    <section class="card max-w-md p-8 text-center">
      <h1 class="text-2xl font-semibold tracking-tight">404</h1>
      <p class="mt-1 text-sm text-ink-muted">The page you're looking for doesn't exist.</p>
      <button
        type="button"
        class="mt-4 inline-flex items-center justify-center rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
        @click="router.push({ name: 'dashboard' })"
      >
        Back to dashboard
      </button>
    </section>
  </div>
</template>
