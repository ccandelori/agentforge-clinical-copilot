import { describe, it, expect } from 'vitest'
import { formatFhirDate } from '@/utils/formatDate'

describe('formatFhirDate', () => {
  it('returns em-dash for null', () => {
    expect(formatFhirDate(null)).toBe('—')
  })

  it('returns em-dash for undefined', () => {
    expect(formatFhirDate(undefined)).toBe('—')
  })

  it('returns em-dash for an empty string', () => {
    expect(formatFhirDate('')).toBe('—')
  })

  it('parses YYYY-MM-DD as a local date (no TZ shift)', () => {
    expect(formatFhirDate('2020-03-15')).toMatch(/Mar\s*15,?\s*2020/)
  })

  it('parses ISO datetime strings with timezone info', () => {
    // The day in the formatted output depends on the runner's TZ;
    // we only assert the year is correct to keep the test
    // TZ-agnostic.
    expect(formatFhirDate('2024-06-15T18:00:00Z')).toMatch(/2024/)
  })

  it('returns the input string when the date is unparseable', () => {
    expect(formatFhirDate('not a date')).toBe('not a date')
  })
})
