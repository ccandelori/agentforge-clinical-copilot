import { customRef, type Ref } from 'vue'

/**
 * A ref whose value updates only after `ms` milliseconds have elapsed
 * without a new write. Reads always reflect the most recently committed
 * value, not the in-flight write.
 *
 * Useful for search-as-you-type inputs where we want to avoid firing a
 * fetch on every keystroke.
 */
export function useDebouncedRef<T>(initial: T, ms = 250): Ref<T> {
  let timeout: ReturnType<typeof setTimeout> | null = null
  let current: T = initial

  return customRef<T>((track, trigger) => ({
    get(): T {
      track()
      return current
    },
    set(next: T): void {
      if (timeout !== null) {
        clearTimeout(timeout)
      }
      timeout = setTimeout(() => {
        current = next
        timeout = null
        trigger()
      }, ms)
    },
  }))
}
