<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'

interface Props {
  modelValue: string
  label?: string
  placeholder?: string
  rows?: number
  maxRows?: number
  id?: string
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  placeholder: '',
  rows: 3,
  maxRows: 24,
  id: '',
  disabled: false,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const autoId = useId()
const fieldId = computed<string>(() => props.id || `txt-${autoId}`)

const textareaEl = ref<HTMLTextAreaElement | null>(null)

function resize(): void {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  // Max height = maxRows * line-height (24px ≈ text-sm/leading-6)
  const lineHeight = 24
  const max = props.maxRows * lineHeight
  const next = Math.min(el.scrollHeight, max)
  el.style.height = `${next}px`
  el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
}

function onInput(ev: Event): void {
  const target = ev.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
  resize()
}

watch(
  () => props.modelValue,
  () => {
    void nextTick(resize)
  },
)

onMounted(() => {
  resize()
})
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      v-if="label"
      :for="fieldId"
      class="text-sm font-medium text-ink"
    >
      {{ label }}
    </label>
    <textarea
      :id="fieldId"
      ref="textareaEl"
      :value="modelValue"
      :rows="rows"
      :placeholder="placeholder"
      :disabled="disabled"
      class="w-full resize-none rounded-lg border border-line bg-surface px-3 py-2 text-sm leading-6 text-ink placeholder:text-ink-muted focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/30 disabled:cursor-not-allowed disabled:opacity-50"
      @input="onInput"
    />
  </div>
</template>
