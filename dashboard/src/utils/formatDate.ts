// Shared date formatter for FHIR `date` and `dateTime` strings used
// across cards. FHIR `date` (YYYY-MM-DD) parses as UTC midnight in
// modern JS — formatted in a negative-offset locale that shifts the
// displayed day back by one. We parse those as local-date instead;
// datetime strings (which carry timezone info) fall through to the
// standard parser.

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

export function formatFhirDate(iso: string | null | undefined): string {
  if (iso === null || iso === undefined || iso === '') return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number) as [number, number, number]
    return dateFormatter.format(new Date(y, m - 1, d))
  }
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormatter.format(parsed)
}
