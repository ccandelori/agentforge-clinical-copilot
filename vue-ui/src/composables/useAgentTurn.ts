import { ref, type Ref } from 'vue'

// Wave 3 wiring: replaces vue-ui's canned typewriter with the real BFF
// `/api/agent/turn` round-trip. Ported from
// `dashboard/src/composables/useAgentTurn.ts`. The sidecar owns identity,
// JWT minting, and `RequestContext` construction; this composable only
// shapes the body and surfaces a typed result.
//
// Tokens are never visible to JS — `credentials: 'same-origin'` instructs
// fetch() to attach the HttpOnly session cookie set by /auth/callback so
// the sidecar can authenticate the request. The Vite dev proxy
// (Agent 1's responsibility) forwards `/api/*` to the sidecar host.
//
// Response is buffered JSON, not SSE: the production path on
// dashboard-port is `{ reply, citations[] }` and the recon (sec 2,
// AgentForge sidecar bridge) confirms the legacy SSE route is behind a
// flag.

export type AgentTurnStatus = 'idle' | 'loading' | 'success' | 'error'

/**
 * Citation kind — controls the badge/icon used in the citations pane.
 *
 * Mirrors the `kind` discriminator the sidecar attaches to citation
 * payloads (T38.14 / Task 24). Reconciled to dashboard-port's exact
 * set (commit 71badfcc5): unknown kinds are dropped at the boundary in
 * {@link parseCitations} rather than passed through opaquely.
 */
export type CitationKind =
  | 'note'
  | 'lab'
  | 'med'
  | 'problem'
  | 'allergy'

/**
 * Citation attached to an assistant reply.
 *
 * Shape matches the documented sidecar response field. Ids are sidecar-
 * provided; downstream UI (citation pill click → CitationsPane scroll
 * target) keys off them.
 */
export interface Citation {
  readonly id: string
  /** Human-readable source label, e.g. "Note 2024-09-12" or "Lab Result". */
  readonly source: string
  readonly excerpt: string
  /** ISO date or short date string for grouping/display. */
  readonly date: string
  readonly kind: CitationKind
  /**
   * Free-form provenance string from the sidecar (resource type +
   * resource id, link to source, etc). Optional — older sidecar
   * payloads may not carry it.
   */
  readonly provenance?: string
}

export interface AgentTurnRequest {
  message: string
  /**
   * FHIR Patient resource UUID. The BFF route resolves this server-side
   * into the integer ``patient_data.pid`` the agent JWT carries. See
   * docs/adr/0001-dashboard-auth-bridging.md §5.
   *
   * vue-ui's drawer is not (yet) scope-aware (chart/intake/research),
   * so callers derive this from `useRoute().params.id` when on
   * `/patients/:id` and pass `undefined` otherwise. The sidecar then
   * handles the no-patient case.
   */
  patient_uuid?: string
  session_id?: string
}

/**
 * Resolved agent turn — what `send()` returns to the caller.
 *
 * Citations are always normalised to an array (empty when the sidecar
 * omits the field) so the drawer never has to ?? everywhere.
 */
export interface AgentTurnResult {
  readonly reply: string
  readonly citations: readonly Citation[]
}

interface AgentTurnResponseBody {
  reply: string
  citations?: unknown
}

const ALLOWED_KINDS: ReadonlySet<CitationKind> = new Set<CitationKind>([
  'note',
  'lab',
  'med',
  'problem',
  'allergy',
])

const REQUEST_TIMEOUT_MS = 30_000

function parseCitation(raw: unknown): Citation | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  if (typeof o.id !== 'string' || o.id.length === 0) return null
  if (typeof o.source !== 'string') return null
  if (typeof o.excerpt !== 'string') return null
  if (typeof o.date !== 'string') return null
  if (typeof o.kind !== 'string') return null
  if (!ALLOWED_KINDS.has(o.kind as CitationKind)) return null
  const out: Citation = {
    id: o.id,
    source: o.source,
    excerpt: o.excerpt,
    date: o.date,
    kind: o.kind as CitationKind,
    ...(typeof o.provenance === 'string' ? { provenance: o.provenance } : {}),
  }
  return out
}

function parseCitations(raw: unknown): readonly Citation[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) return []
  const out: Citation[] = []
  for (const item of raw) {
    const parsed = parseCitation(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

export interface UseAgentTurn {
  status: Ref<AgentTurnStatus>
  error: Ref<Error | null>
  send: (req: AgentTurnRequest) => Promise<AgentTurnResult>
}

export function useAgentTurn(): UseAgentTurn {
  const status = ref<AgentTurnStatus>('idle')
  const error = ref<Error | null>(null)

  async function send(req: AgentTurnRequest): Promise<AgentTurnResult> {
    status.value = 'loading'
    error.value = null

    const body: Record<string, unknown> = {
      message: req.message,
    }
    if (req.patient_uuid !== undefined) {
      body.patient_uuid = req.patient_uuid
    }
    if (req.session_id !== undefined) {
      body.session_id = req.session_id
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(() => {
      controller.abort()
    }, REQUEST_TIMEOUT_MS)

    try {
      const res = await fetch('/api/agent/turn', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!res.ok) {
        throw new Error(
          `Agent request failed (HTTP ${res.status}). Try again in a moment.`,
        )
      }
      const parsed = (await res.json()) as Partial<AgentTurnResponseBody>
      if (typeof parsed.reply !== 'string') {
        throw new Error('Agent response was malformed.')
      }
      const citations = parseCitations(parsed.citations)
      status.value = 'success'
      return { reply: parsed.reply, citations }
    } catch (caught) {
      let friendly: Error
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        friendly = new Error(
          'Agent request timed out after 30 seconds. Please try again.',
        )
      } else if (caught instanceof Error) {
        friendly = caught
      } else {
        friendly = new Error('Agent request failed.')
      }
      error.value = friendly
      status.value = 'error'
      throw friendly
    } finally {
      clearTimeout(timeoutId)
    }
  }

  return { status, error, send }
}
