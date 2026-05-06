<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const message = ref('Completing sign-in…')

onMounted(async () => {
  try {
    const result = await auth.handleCallback()
    const state = result.state as { targetPath?: string } | null | undefined
    const target =
      state !== null && state !== undefined && typeof state.targetPath === 'string'
        ? state.targetPath
        : '/'
    await router.replace(target)
  } catch {
    // Error already captured on the store; route back to login so the user
    // sees the failure banner.
    message.value = 'Sign-in failed. Redirecting to login…'
    await router.replace('/login')
  }
})
</script>

<template>
  <main class="container py-5 d-flex align-items-center justify-content-center">
    <div class="text-center">
      <div class="spinner-border text-primary mb-3" role="status" aria-hidden="true"></div>
      <p class="text-muted">{{ message }}</p>
    </div>
  </main>
</template>
