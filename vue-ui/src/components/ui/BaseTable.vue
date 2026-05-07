<script setup lang="ts" generic="TRow extends Record<string, unknown>">
export interface TableColumn<T> {
  readonly key: string
  readonly label: string
  readonly width?: string
  readonly align?: 'left' | 'right' | 'center'
  readonly accessor?: (row: T) => unknown
}

export interface BaseTableProps<TRowType extends Record<string, unknown>> {
  columns: ReadonlyArray<TableColumn<TRowType>>
  rows: ReadonlyArray<TRowType>
  rowKey: keyof TRowType | ((row: TRowType) => string)
  loading?: boolean
}

const props = withDefaults(defineProps<BaseTableProps<TRow>>(), {
  loading: false,
})

defineEmits<{
  (e: 'row-click', row: TRow): void
}>()

function keyFor(row: TRow): string {
  if (typeof props.rowKey === 'function') return props.rowKey(row)
  return String(row[props.rowKey])
}

function valueFor(row: TRow, col: TableColumn<TRow>): unknown {
  if (col.accessor) return col.accessor(row)
  return row[col.key as keyof TRow]
}

function alignClass(col: TableColumn<TRow>): string {
  switch (col.align) {
    case 'right':
      return 'text-right'
    case 'center':
      return 'text-center'
    default:
      return 'text-left'
  }
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border border-line">
    <table class="w-full text-sm">
      <thead class="bg-surface-2 text-xs uppercase tracking-wide text-ink-muted">
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :style="col.width ? { width: col.width } : undefined"
            class="px-4 py-2 font-semibold"
            :class="alignClass(col)"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line bg-surface">
        <tr v-if="loading">
          <td :colspan="columns.length" class="px-4 py-6 text-center text-ink-muted">
            Loading…
          </td>
        </tr>
        <tr v-else-if="rows.length === 0">
          <td :colspan="columns.length" class="px-4 py-6 text-center text-ink-muted">
            <slot name="empty">No results.</slot>
          </td>
        </tr>
        <tr
          v-for="row in rows"
          v-else
          :key="keyFor(row)"
          class="hover:bg-surface-2"
          @click="$emit('row-click', row)"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-2.5"
            :class="alignClass(col)"
          >
            <slot :name="`cell-${col.key}`" :row="row" :value="valueFor(row, col)">
              {{ valueFor(row, col) }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
