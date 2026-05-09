import { ref, type Ref } from 'vue'

import {
  parseIntakeExtraction,
  type IntakeExtraction,
} from './parseIntakeExtraction'
import {
  parseLabExtraction,
  type LabExtraction,
} from './parseLabExtraction'
import type { DocumentType } from './useDocumentUpload'

// Wave 3 wiring: replaces vue-ui's canned typewriter with the real BFF
// `/api/agent/turn` round-trip. The sidecar owns identity, JWT minting,
// and `RequestContext` construction; this composable only shapes the
// body and surfaces a typed result.
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
 * Citation source type — controls the badge/icon used in the citations pane.
 *
 * Mirrors the sidecar's :class:`agentforge.schemas.citation.SourceType`
 * enum (W2_ARCHITECTURE.md §2.2). The dashboard renders each source
 * type with a distinct visual treatment so a clinician can tell at a
 * glance whether a claim is grounded in the patient's chart, a
 * scanned document, or a published guideline. Unknown source types
 * are dropped at the boundary in {@link parseCitations}.
 */
export type CitationSourceType =
  | 'openemr_record'
  | 'guideline'
  | 'lab_pdf'
  | 'intake_form'

/**
 * W2 machine-readable citation attached to an assistant reply.
 *
 * Shape mirrors the sidecar :class:`agentforge.dashboard_auth.turn_route.AgentTurnCitation`
 * exactly — the BFF flattens
 * :class:`agentforge.schemas.citation.Citation` plus the chart-record
 * projection into this surface so the dashboard can trace every
 * clinical claim back to its source without a second round-trip.
 *
 * Identity for click → scroll-to-card uses
 * ``${source_type}/${field_or_chunk_id}`` (the same pair the BFF
 * dedupes on).
 */
export interface Citation {
  readonly source_type: CitationSourceType
  /**
   * Stable handle for the source: FHIR/OpenEMR record id, guideline
   * document id, or scanned-document id. Always non-empty.
   */
  readonly source_id: string
  /**
   * Human-readable locator: ``"page 2"`` for documents, ``"Section 4.1"``
   * for guideline chunks. ``null`` for chart-resident records that
   * have no page/section concept (the BFF substitutes the row's date
   * here when one is available).
   */
  readonly page_or_section: string | null
  /**
   * Stable inner handle: ``"<record_type>/<record_id>"`` for chart
   * records, retrieval ``chunk_id`` for guideline chunks, extraction
   * field key for scanned documents. ``null`` only on the rare
   * fallback path where the BFF could not resolve the bracket-tag.
   */
  readonly field_or_chunk_id: string | null
  /**
   * The literal extracted value or quoted text the claim is grounded
   * in. Capped at 4 KB by the BFF; the UI further truncates for
   * compact display and reveals the full quote on the "View source"
   * expand toggle.
   */
  readonly quote_or_value: string | null
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
  /**
   * Optional OpenEMR ``documents.id`` (as a string) for a PDF the
   * clinician just attached via the chat composer's file picker. The
   * sidecar's W2 graph picks this up and routes the turn through the
   * vision-extractor node before answering. ``document_id`` rides one
   * turn — callers clear their pending state immediately after passing
   * it in so a follow-up chat message doesn't re-attach the same
   * upload. See ``useDocumentUpload`` for the upload flow that produces
   * this id.
   */
  document_id?: string
  /**
   * Optional vision-extractor dispatch hint paired with ``document_id``.
   *
   * - ``'intake_form'`` → BFF graph routes through ``INTAKE_CONTRACT``
   *   (the demo's primary path).
   * - ``'lab_pdf'`` → BFF graph routes through ``LAB_CONTRACT``.
   *
   * Omitted when no document is attached, or when the caller wants the
   * BFF default (``intake_form``). The dashboard derives this from the
   * filename via :func:`inferDocType` at upload time and stamps it on
   * the {@link PendingAttachment}; the store then forwards it here.
   *
   * See ``sidecar/src/agentforge/dashboard_auth/turn_route.py``
   * (``AgentTurnRequest.doc_type``) for the BFF contract.
   */
  doc_type?: DocumentType
  /**
   * Optional free-text guideline question. When non-empty, the BFF's
   * W2 graph routes the turn to the evidence retriever node (RAG over
   * clinical guidelines) and produces guideline citations alongside
   * the assistant reply.
   *
   * Empty / omitted is the chart-Q&A path: the W2 graph falls back to
   * the W1 iterative loop and never fires the retriever, so chart-only
   * questions don't pay the RAG round-trip.
   *
   * The dashboard exposes this via the "Ask guidelines" toggle in the
   * chat composer (``useAgentForgeStore.guidelineMode``); when on, the
   * store forwards the user's message verbatim as both ``message`` and
   * ``evidence_query``.
   *
   * See ``sidecar/src/agentforge/dashboard_auth/turn_route.py``
   * (``AgentTurnRequest.evidence_query``) for the BFF contract.
   */
  evidence_query?: string
}

/**
 * Discriminated union covering the two structured extractions the W2
 * graph can produce. Lab and intake snapshots have disjoint Pydantic
 * shapes on the sidecar; the parsers below pick the matching one and
 * the dashboard panel dispatches off the discriminator field.
 *
 * `kind` is added at the parser boundary, NOT on the wire — the BFF
 * route ships the raw `model_dump(mode="json")` of either
 * `IntakeFormExtraction` or `LabPdfExtraction`. Tagging here keeps the
 * dispatch in `AgentMessage.vue` to a single literal-union check
 * without revisiting the wire shape.
 */
export type ExtractionResult =
  | ({ readonly kind: 'intake' } & IntakeExtraction)
  | ({ readonly kind: 'lab' } & LabExtraction)

/**
 * Resolved agent turn — what `send()` returns to the caller.
 *
 * Citations are always normalised to an array (empty when the sidecar
 * omits the field) so the drawer never has to ?? everywhere.
 */
export interface AgentTurnResult {
  readonly reply: string
  readonly citations: readonly Citation[]
  /**
   * Structured extraction surfaced when the turn included a scanned
   * document. ``undefined`` when the turn was a chart Q&A (no document
   * attached) or when the W2 graph chose not to extract. Tagged so the
   * dashboard can render the matching panel (`ExtractionPanel` for
   * intake, `LabPanel` for lab) without re-sniffing the shape.
   */
  readonly extraction?: ExtractionResult
}

export type { IntakeExtraction } from './parseIntakeExtraction'
export type { LabExtraction } from './parseLabExtraction'

interface AgentTurnResponseBody {
  reply: string
  citations?: unknown
  extraction?: unknown
}

const ALLOWED_SOURCE_TYPES: ReadonlySet<CitationSourceType> = new Set<CitationSourceType>([
  'openemr_record',
  'guideline',
  'lab_pdf',
  'intake_form',
])

// 120s accommodates the W2 document-extraction path (PDF render →
// VLM page-by-page → verifier → synthesizer) which can take 30–60s
// on first cold call. Chart-Q&A turns finish in <10s — no penalty.
// A future iteration should switch to SSE so timeout is a soft
// upper bound, not a hard wall.
const REQUEST_TIMEOUT_MS = 120_000

function parseCitation(raw: unknown): Citation | null {
  if (typeof raw !== 'object' || raw === null) return null
  const o = raw as Record<string, unknown>
  if (typeof o.source_type !== 'string') return null
  if (!ALLOWED_SOURCE_TYPES.has(o.source_type as CitationSourceType)) return null
  if (typeof o.source_id !== 'string' || o.source_id.length === 0) return null
  // Optional fields: BFF emits null (not undefined) when unavailable.
  // Accept either to stay forgiving on future shape additions.
  const page_or_section: string | null
    = typeof o.page_or_section === 'string' && o.page_or_section.length > 0
      ? o.page_or_section
      : null
  const field_or_chunk_id: string | null
    = typeof o.field_or_chunk_id === 'string' && o.field_or_chunk_id.length > 0
      ? o.field_or_chunk_id
      : null
  const quote_or_value: string | null
    = typeof o.quote_or_value === 'string' && o.quote_or_value.length > 0
      ? o.quote_or_value
      : null
  return {
    source_type: o.source_type as CitationSourceType,
    source_id: o.source_id,
    page_or_section,
    field_or_chunk_id,
    quote_or_value,
  }
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

/**
 * Stable identity for a citation — used as Vue list keys and as the
 * scroll-target id when a CitationPill is clicked. Mirrors the BFF's
 * dedup key (``source_type`` + ``field_or_chunk_id``); falls back to
 * ``source_id`` when ``field_or_chunk_id`` is missing so the key is
 * always populated.
 */
export function citationKey(c: Citation): string {
  return `${c.source_type}/${c.field_or_chunk_id ?? c.source_id}`
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
    if (req.document_id !== undefined) {
      body.document_id = req.document_id
    }
    if (req.doc_type !== undefined) {
      body.doc_type = req.doc_type
    }
    if (req.evidence_query !== undefined) {
      body.evidence_query = req.evidence_query
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
      if (res.status === 401) {
        // Cookie expired or sidecar dropped the session — bounce the SPA
        // back through the auth flow. main.ts listens for this and
        // navigates to /login after re-hydrating.
        window.dispatchEvent(new CustomEvent('auth:unauthorized'))
        throw new Error('Your session expired. Please sign in again.')
      }
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
      // P1.2: try the lab parser first — its discriminator is strict
      // (requires `values[]`, rejects intake-shaped payloads) so a
      // successful lab parse is unambiguous. Fall through to intake
      // for the demo's primary path. Both parsers return `null` on
      // shape mismatch; if neither claims the payload we emit no
      // extraction and the chat bubble renders without a panel.
      const lab = parseLabExtraction(parsed.extraction)
      const intake = lab === null
        ? parseIntakeExtraction(parsed.extraction)
        : null
      const extraction: ExtractionResult | undefined = lab !== null
        ? { kind: 'lab', ...lab }
        : intake !== null
          ? { kind: 'intake', ...intake }
          : undefined
      status.value = 'success'
      return {
        reply: parsed.reply,
        citations,
        ...(extraction !== undefined ? { extraction } : {}),
      }
    } catch (caught) {
      let friendly: Error
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        friendly = new Error(
          'Agent request timed out after 2 minutes. Please try again.',
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
