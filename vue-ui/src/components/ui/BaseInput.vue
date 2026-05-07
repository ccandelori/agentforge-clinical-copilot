<script setup lang="ts">
import { computed, useId } from 'vue'

interface Props {
  modelValue?: string
  label?: string
  hint?: string
  error?: string
  type?: string
  placeholder?: string
  disabled?: boolean
  required?: boolean
  id?: string
  autocomplete?: string
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
  label: '',
  hint: '',
  error: '',
  type: 'text',
  placeholder: '',
  disabled: false,
  required: false,
  id: '',
  autocomplete: 'off',
})

defineEmits<{
  (e: 'update:modelValue', v: string): void
}>()

const autoId = useId()
const fieldId = computed<string>(() => props.id || `input-${autoId}`)
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      v-if="label"
      :for="fieldId"
      class="text-sm font-medium text-ink"
    >
      {{ label }}<span v-if="required" class="ml-0.5 text-danger-600">*</span>
    </label>

    <div
      class="flex items-center rounded-lg border bg-surface focus-within:ring-2 focus-within:ring-primary-500/30"
      :class="
        error
          ? 'border-danger-500 focus-within:border-danger-500'
          : 'border-line focus-within:border-primary-500'
      "
    >
      <span v-if="$slots.prefix" class="flex items-center pl-3 text-ink-muted">
        <slot name="prefix" />
      </span>
      <input
        :id="fieldId"
        :value="modelValue"
        :type="type"
        :placeholder="placeholder"
        :disabled="disabled"
        :required="required"
        :autocomplete="autocomplete"
        class="block w-full bg-transparent px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        @input="
          $emit(
            'update:modelValue',
            ($event.target as HTMLInputElement).value,
          )
        "
      />
      <span v-if="$slots.suffix" class="flex items-center pr-3 text-ink-muted">
        <slot name="suffix" />
      </span>
    </div>

    <p v-if="error" class="text-xs text-danger-600">{{ error }}</p>
    <p v-else-if="hint" class="text-xs text-ink-muted">{{ hint }}</p>
  </div>
</template>
