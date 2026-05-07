import { computed, type ComputedRef, type MaybeRefOrGetter, toValue } from 'vue'

const DIVISIONS: ReadonlyArray<{
  readonly amount: number
  readonly unit: Intl.RelativeTimeFormatUnit
}> = [
  { amount: 60, unit: 'second' },
  { amount: 60, unit: 'minute' },
  { amount: 24, unit: 'hour' },
  { amount: 7, unit: 'day' },
  { amount: 4.34524, unit: 'week' },
  { amount: 12, unit: 'month' },
  { amount: Number.POSITIVE_INFINITY, unit: 'year' },
]

const FORMATTER = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/**
 * Format a date as a relative time string ("3 days ago", "in 2 weeks").
 *
 * Returns an empty string for null/undefined/invalid inputs so callers
 * can render conditionally without further checks.
 */
export function relativeTime(
  date: Date | string | number | null | undefined,
  now: Date = new Date(),
): string {
  if (date === null || date === undefined) return ''
  const target = date instanceof Date ? date : new Date(date)
  const ts = target.getTime()
  if (Number.isNaN(ts)) return ''

  let diff = (ts - now.getTime()) / 1000 // seconds
  for (const { amount, unit } of DIVISIONS) {
    if (Math.abs(diff) < amount) {
      return FORMATTER.format(Math.round(diff), unit)
    }
    diff /= amount
  }
  return FORMATTER.format(Math.round(diff), 'year')
}

/**
 * Reactive wrapper that re-evaluates when the source value changes.
 * Use in templates when the value is a ref or getter.
 */
export function useRelativeTime(
  source: MaybeRefOrGetter<Date | string | number | null | undefined>,
): ComputedRef<string> {
  return computed(() => relativeTime(toValue(source)))
}
