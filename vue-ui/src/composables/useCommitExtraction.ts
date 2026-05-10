import { ref, type Ref } from 'vue'

/**
 * Composable for the BFF intake-promotion call (Gap 2).
 *
 * `<ExtractionPanel>` collects the rows the clinician approved,
 * dispatches them through this composable, and the BFF
 * (`POST /api/agent/promote/intake`) lands one structured EHR row
 * per accepted item. After a successful commit, callers should
 * invalidate any in-memory patient bundle (see `usePatient`'s
 * exported `invalidatePatientCache`) so the dashboard cards refresh
 * with the new chart rows.
 *
 * The safety property here is the per-row checkbox + explicit
 * Commit click on the `ExtractionPanel`. The composable does no
 * client-side filtering or implicit defaults — every item it sends
 * was approved by the clinician.
 *
 * Tokens are never visible to JS — `credentials: 'same-origin'`
 * attaches the HttpOnly session cookie set by `/auth/callback`. The
 * Vite dev proxy forwards `/api/*` to the sidecar host (same setup
 * as `useAgentTurn` and `useDocumentUpload`).
 */

export type CommitItemKind =
  | 'allergy'
  | 'medical_problem'
  | 'medication'
  | 'family_history'

export type CommitStatus = 'idle' | 'loading' | 'success' | 'error'

/**
 * One row to commit. ``kind`` mirrors the closed set on the sidecar
 * (`PromoteItemKind`) and the PHP writer (`IntakePromotionWriter`'s
 * class constants); the four enum values are wired together across
 * three layers and adding a new kind is a coordinated change.
 *
 * ``title`` is what lands in `lists.title` — the substance for
 * allergies, condition for problems, drug name for medications,
 * "relative: condition" for family history. ``details`` is optional
 * and lands appended to `lists.comments`.
 */
export interface CommitItem {
  readonly kind: CommitItemKind
  readonly title: string
  readonly details?: string
}

export interface CommitExtractionRequest {
  /**
   * FHIR Patient resource UUID. The sidecar resolves this server-
   * side into the integer ``patient_data.pid`` the JWT carries.
   */
  readonly patientUuid: string
  readonly items: readonly CommitItem[]
  /** Optional audit/lineage hint surfaced by `useAgentTurn`. */
  readonly questionnaireResponseId?: string
  /** Optional audit/lineage hint surfaced by the upload flow. */
  readonly documentId?: string
}

/** One handle returned per row written. Mirrors the PHP receipt. */
export interface CommitResultHandle {
  readonly kind: CommitItemKind
  readonly listsId: number
  readonly title: string
}

export interface CommitResult {
  readonly count: number
  readonly promoted: readonly CommitResultHandle[]
}

interface CommitResponseBody {
  count?: unknown
  promoted?: unknown
}

/**
 * 30s — generous for a small batched DB write. The PHP side wraps
 * the inserts in a transactional() call; even a 50-item batch lands
 * in well under a second on dev-easy. A timeout signals the
 * sidecar / OpenEMR isn't reachable, not that the write is slow.
 */
const REQUEST_TIMEOUT_MS = 30_000

const ALLOWED_KINDS: ReadonlySet<CommitItemKind> = new Set<CommitItemKind>([
  'allergy',
  'medical_problem',
  'medication',
  'family_history',
])

function parseHandle(raw: unknown): CommitResultHandle | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  if (typeof o.kind !== 'string') return null
  if (!ALLOWED_KINDS.has(o.kind as CommitItemKind)) return null
  if (typeof o.lists_id !== 'number' || !Number.isFinite(o.lists_id)) return null
  if (typeof o.title !== 'string') return null
  return {
    kind: o.kind as CommitItemKind,
    listsId: o.lists_id,
    title: o.title,
  }
}

function parseHandles(raw: unknown): readonly CommitResultHandle[] {
  if (!Array.isArray(raw)) return []
  const out: CommitResultHandle[] = []
  for (const item of raw) {
    const parsed = parseHandle(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

export interface UseCommitExtraction {
  status: Ref<CommitStatus>
  error: Ref<Error | null>
  commit: (req: CommitExtractionRequest) => Promise<CommitResult>
}

export function useCommitExtraction(): UseCommitExtraction {
  const status = ref<CommitStatus>('idle')
  const error = ref<Error | null>(null)

  async function commit(req: CommitExtractionRequest): Promise<CommitResult> {
    status.value = 'loading'
    error.value = null

    if (req.items.length === 0) {
      const empty = new Error('No items selected to commit.')
      error.value = empty
      status.value = 'error'
      throw empty
    }

    const body: Record<string, unknown> = {
      patient_uuid: req.patientUuid,
      items: req.items.map((it) => {
        const item: Record<string, unknown> = {
          kind: it.kind,
          title: it.title,
        }
        if (it.details !== undefined && it.details.length > 0) {
          item.details = it.details
        }
        return item
      }),
    }
    if (req.questionnaireResponseId !== undefined) {
      body.questionnaire_response_id = req.questionnaireResponseId
    }
    if (req.documentId !== undefined) {
      body.document_id = req.documentId
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(() => {
      controller.abort()
    }, REQUEST_TIMEOUT_MS)

    try {
      const res = await fetch('/api/agent/promote/intake', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (res.status === 401) {
        // Cookie expired or sidecar dropped the session — bounce the
        // SPA back through the auth flow. main.ts listens for this
        // and navigates to /login after re-hydrating.
        window.dispatchEvent(new CustomEvent('auth:unauthorized'))
        throw new Error('Your session expired. Please sign in again.')
      }
      if (!res.ok) {
        throw new Error(
          `Commit failed (HTTP ${res.status}). Try again in a moment.`,
        )
      }
      const parsed = (await res.json()) as Partial<CommitResponseBody>
      const promoted = parseHandles(parsed.promoted)
      const count = typeof parsed.count === 'number' && Number.isFinite(parsed.count)
        ? parsed.count
        : promoted.length
      status.value = 'success'
      return { count, promoted }
    } catch (caught) {
      let friendly: Error
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        friendly = new Error('Commit request timed out. Please try again.')
      } else if (caught instanceof Error) {
        friendly = caught
      } else {
        friendly = new Error('Commit request failed.')
      }
      error.value = friendly
      status.value = 'error'
      throw friendly
    } finally {
      clearTimeout(timeoutId)
    }
  }

  return { status, error, commit }
}
