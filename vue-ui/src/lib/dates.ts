/**
 * Native-Date helpers for the calendar/scheduler. No external date library.
 *
 * All helpers are pure: they never mutate their inputs and always return
 * a new `Date` instance (or a primitive). Time-of-day operations are local-
 * timezone — that is what scheduling UIs almost always want.
 */

/** Return a new Date set to 00:00:00.000 local-time on the same calendar day. */
export function startOfDay(date: Date): Date {
  const d = new Date(date.getTime())
  d.setHours(0, 0, 0, 0)
  return d
}

/** Return a new Date `days` days after `date` (negative = earlier). */
export function addDays(date: Date, days: number): Date {
  const d = new Date(date.getTime())
  d.setDate(d.getDate() + days)
  return d
}

/**
 * First instant of the calendar week containing `date`.
 *
 * `weekStartsOn` defaults to 0 (Sunday). Pass 1 for ISO/Monday weeks.
 */
export function startOfWeek(date: Date, weekStartsOn: 0 | 1 = 0): Date {
  const d = startOfDay(date)
  const day = d.getDay()
  const diff = (day - weekStartsOn + 7) % 7
  return addDays(d, -diff)
}

/** Last instant of the calendar week containing `date` (23:59:59.999). */
export function endOfWeek(date: Date, weekStartsOn: 0 | 1 = 0): Date {
  const start = startOfWeek(date, weekStartsOn)
  const end = addDays(start, 6)
  end.setHours(23, 59, 59, 999)
  return end
}

/** First day of the month for `date`, at 00:00:00.000. */
export function startOfMonth(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth(), 1, 0, 0, 0, 0)
  return d
}

/** Last day of the month for `date`, at 23:59:59.999. */
export function endOfMonth(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth() + 1, 0, 23, 59, 59, 999)
  return d
}

/** Format a Date as a short local time, e.g. "9:30 AM". */
export function formatTime(date: Date): string {
  return date.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** Format a Date as a long, human-readable date, e.g. "Wednesday, May 7, 2026". */
export function formatDateLong(date: Date): string {
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

/** Format a Date as a short month label, e.g. "May 2026". */
export function formatMonthLong(date: Date): string {
  return date.toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  })
}

/** True if `a` and `b` are the same calendar day (local). */
export function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
  )
}

/** Clamp `n` to the inclusive range `[min, max]`. */
export function clamp(n: number, min: number, max: number): number {
  if (n < min) return min
  if (n > max) return max
  return n
}

/** Stable YYYY-MM-DD key for caching by calendar day (local). */
export function dayKey(date: Date): string {
  const y = date.getFullYear().toString().padStart(4, '0')
  const m = (date.getMonth() + 1).toString().padStart(2, '0')
  const d = date.getDate().toString().padStart(2, '0')
  return `${y}-${m}-${d}`
}

/** Minutes-since-midnight (local) for a Date. */
export function minutesOfDay(date: Date): number {
  return date.getHours() * 60 + date.getMinutes()
}
