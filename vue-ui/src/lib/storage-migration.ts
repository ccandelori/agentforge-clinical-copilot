/**
 * One-time, idempotent migration of any pre-fix encounter drafts that
 * were written to `localStorage` by older builds. Drafts contain PHI
 * (chief complaint, HPI, exam, assessment, plan) and must live in
 * `sessionStorage` so they expire with the browser session.
 *
 * Runs once on app boot from `main.ts`. Safe to invoke repeatedly:
 * a no-op when there is nothing to migrate.
 */

const ENCOUNTER_DRAFT_PREFIX = 'encounter-draft.'

/**
 * Moves every `encounter-draft.*` entry from localStorage to sessionStorage
 * and removes the localStorage copy. Logs the migrated count once when
 * any work was done.
 */
export function migrateEncounterDraftsFromLocalStorage(): void {
  if (typeof window === 'undefined') return

  let local: Storage
  let session: Storage
  try {
    local = window.localStorage
    session = window.sessionStorage
  } catch {
    // Storage disabled (private mode, restrictive iframe) — nothing to do.
    return
  }

  // Snapshot keys first; we mutate localStorage as we iterate.
  const keys: string[] = []
  try {
    for (let i = 0; i < local.length; i++) {
      const key = local.key(i)
      if (key !== null && key.startsWith(ENCOUNTER_DRAFT_PREFIX)) {
        keys.push(key)
      }
    }
  } catch {
    return
  }

  if (keys.length === 0) return

  let migrated = 0
  for (const key of keys) {
    try {
      const value = local.getItem(key)
      if (value === null) continue
      // Don't clobber an in-flight session draft for the same encounter.
      if (session.getItem(key) === null) {
        session.setItem(key, value)
      }
      local.removeItem(key)
      migrated++
    } catch {
      // Per-key failure (quota etc.) — skip and continue.
    }
  }

  if (migrated > 0) {
    // eslint-disable-next-line no-console
    console.info(
      `[storage-migration] Moved ${migrated} encounter draft(s) from localStorage to sessionStorage (HIPAA).`,
    )
  }
}
