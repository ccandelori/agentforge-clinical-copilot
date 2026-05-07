import { ref, type Ref } from 'vue'

/**
 * Multipart document upload composable for the AgentForge drawer.
 *
 * The clinician picks a PDF (or image) from the chat composer's file
 * picker; this composable POSTs the bytes to the BFF, which in turn
 * brokers the OpenEMR session-authenticated upload + audit
 * (:class:`UploadDocumentController`). The successful response carries
 * a numeric ``document_id``; the dashboard surfaces it as a string so
 * downstream code does not have to think about JSON-number rounding
 * for IDs that happen to fit in a double.
 *
 * **Why we go via the BFF, not directly to OpenEMR.** OpenEMR's
 * `/agentforge/upload_document` route is session-authenticated against
 * the OpenEMR PHP session cookie. The vue-ui SPA holds only the BFF's
 * HttpOnly cookie — the OpenEMR session cookie lives at a different
 * origin (the OpenEMR PHP host) and is not in the SPA's cookie jar in
 * dev or production. The BFF endpoint at ``/api/agent/upload`` is
 * therefore expected to either (a) re-authenticate the session via the
 * existing internal-JWT bridge and call OpenEMR's upload route
 * server-side, or (b) speak directly to the document store using the
 * same writer the PHP route uses. Either way, the SPA never has to
 * surface the OpenEMR session cookie.
 *
 * **No PHI in browser storage.** The composable does not persist the
 * file or any returned id. It returns the ``document_id`` to the
 * caller and lets the caller (currently :file:`AgentChatPane.vue`)
 * keep it in component state until the next agent turn fires.
 */

/** Result of a successful upload. ``document_id`` is the OpenEMR
 * documents-table primary key, normalised to a string. */
export interface UploadResult {
  readonly document_id: string
}

export interface UseDocumentUpload {
  /** ``true`` while a request is in flight. Surfaced for spinner UI. */
  isUploading: Ref<boolean>
  /** Last error from a failed upload, or ``null`` otherwise. */
  error: Ref<Error | null>
  /**
   * Multipart-POST ``file`` to the BFF upload route.
   *
   * Throws on non-2xx responses, network errors, and malformed JSON
   * payloads (specifically a payload missing ``document_id``). The
   * thrown error carries a user-facing message; callers should surface
   * it as-is in the chat error bubble.
   */
  uploadDocument: (file: File, patientUuid: string) => Promise<UploadResult>
}

const UPLOAD_URL = '/api/agent/upload'
const REQUEST_TIMEOUT_MS = 60_000

interface UploadResponseBody {
  success?: boolean
  document_id?: unknown
  error?: unknown
}

function parseDocumentId(raw: unknown): string | null {
  if (typeof raw === 'string' && raw.length > 0) return raw
  if (typeof raw === 'number' && Number.isFinite(raw) && raw > 0) {
    return String(raw)
  }
  return null
}

export function useDocumentUpload(): UseDocumentUpload {
  const isUploading = ref<boolean>(false)
  const error = ref<Error | null>(null)

  async function uploadDocument(
    file: File,
    patientUuid: string,
  ): Promise<UploadResult> {
    isUploading.value = true
    error.value = null

    const formData = new FormData()
    formData.append('file', file)
    formData.append('patient_uuid', patientUuid)

    const controller = new AbortController()
    const timeoutId = setTimeout(() => {
      controller.abort()
    }, REQUEST_TIMEOUT_MS)

    try {
      // Note: do NOT set Content-Type — the browser must set the
      // multipart boundary itself. Setting it manually corrupts the
      // body (the boundary won't match the headers).
      const res = await fetch(UPLOAD_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        body: formData,
        signal: controller.signal,
      })

      if (res.status === 401) {
        // Same dispatch surface as `useAgentTurn` so router-level
        // listeners can bounce the SPA to /login uniformly.
        window.dispatchEvent(new CustomEvent('auth:unauthorized'))
        throw new Error('Your session expired. Please sign in again.')
      }
      if (!res.ok) {
        throw new Error(
          `Document upload failed (HTTP ${res.status}). Try again in a moment.`,
        )
      }

      const parsed = (await res.json()) as Partial<UploadResponseBody>
      const documentId = parseDocumentId(parsed.document_id)
      if (documentId === null) {
        throw new Error('Upload succeeded but no document_id was returned.')
      }
      return { document_id: documentId }
    } catch (caught) {
      let friendly: Error
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        friendly = new Error(
          'Document upload timed out. Try a smaller file or retry.',
        )
      } else if (caught instanceof Error) {
        friendly = caught
      } else {
        friendly = new Error('Document upload failed.')
      }
      error.value = friendly
      throw friendly
    } finally {
      clearTimeout(timeoutId)
      isUploading.value = false
    }
  }

  return { isUploading, error, uploadDocument }
}
