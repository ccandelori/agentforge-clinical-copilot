<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'

type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

interface Props {
  open: boolean
  title?: string
  closeOnBackdrop?: boolean
  size?: ModalSize
}

const props = withDefaults(defineProps<Props>(), {
  title: '',
  closeOnBackdrop: true,
  size: 'md',
})

const sizeClass: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-3xl',
  xl: 'max-w-5xl',
}

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'close'): void
}>()

function close(): void {
  emit('update:open', false)
  emit('close')
}

function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && props.open) close()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

watch(
  () => props.open,
  (open) => {
    if (typeof document === 'undefined') return
    document.body.style.overflow = open ? 'hidden' : ''
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-40 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
    >
      <div
        class="absolute inset-0 bg-neutral-900/50 backdrop-blur-sm"
        @click="closeOnBackdrop ? close() : null"
      />
      <div
        class="relative z-10 flex max-h-[85vh] w-full flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card-lg"
        :class="sizeClass[props.size]"
      >
        <header
          v-if="title || $slots.title"
          class="flex items-center justify-between gap-4 border-b border-line px-5 py-3"
        >
          <slot name="title">
            <h2 class="text-sm font-semibold tracking-tight">{{ title }}</h2>
          </slot>
          <button
            type="button"
            class="rounded-md p-1 text-ink-muted hover:bg-surface-2 hover:text-ink"
            aria-label="Close"
            @click="close"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </header>
        <div class="min-h-0 flex-1 overflow-y-auto p-5">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="border-t border-line bg-surface-2 px-5 py-3">
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </Teleport>
</template>
