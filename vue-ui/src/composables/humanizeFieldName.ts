/**
 * snake_case extraction field name → human-readable label.
 *
 * The sidecar surfaces demographic field keys verbatim from the LLM JSON
 * (``date_of_birth``, ``chief_concern``, ``mrn`` …). Rendering those raw
 * in the ExtractionPanel reads as machine output. This helper title-cases
 * each token and joins with spaces, with a small acronym map so common
 * FHIR / clinical-record initialisms render as upper-case rather than
 * "Mrn" / "Dob".
 *
 * Lower-case glue words (``of``, ``and``, …) are preserved when they
 * appear as interior tokens, matching newspaper-headline-ish casing
 * (``Date of Birth``, not ``Date Of Birth``).
 */

const ACRONYMS: ReadonlySet<string> = new Set([
    'dob',
    'mrn',
    'npi',
    'ssn',
    'id',
])

const LOWERCASE_GLUE: ReadonlySet<string> = new Set([
    'of',
    'and',
    'or',
    'the',
    'a',
    'an',
])

function titleCase(token: string): string {
    return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase()
}

export function humanizeFieldName(name: string): string {
    const trimmed = name.trim()
    if (trimmed.length === 0) {
        return ''
    }

    const tokens = trimmed
        .split('_')
        .filter((token) => token.length > 0)
        .map((token) => token.toLowerCase())

    if (tokens.length === 0) {
        return ''
    }

    return tokens
        .map((token, idx) => {
            if (ACRONYMS.has(token)) {
                return token.toUpperCase()
            }
            if (idx > 0 && LOWERCASE_GLUE.has(token)) {
                return token
            }
            return titleCase(token)
        })
        .join(' ')
}
