import type { DocumentType } from './useDocumentUpload'

/**
 * Filename → BFF :type:`DocumentType` heuristic.
 *
 * The BFF route's ``_ALLOWED_DOC_TYPES`` is currently ``{lab_pdf,
 * intake_form}``. The chat composer's file picker is doc-type-agnostic,
 * so we sniff the filename for whole-word lab markers; everything else
 * falls back to ``intake_form`` (the demo's primary path).
 *
 * Whole-word matching (``\b…\b``) is deliberate: ``laboratory.pdf``
 * routes through intake (the report-style PDF generator typically
 * names lab fixtures with the panel name, not the word "laboratory"),
 * and ``collaboration-form.pdf`` does not get mis-routed because of the
 * ``lab`` substring.
 *
 * Extracted from ``AgentChatPane.vue`` so the heuristic can be unit-
 * tested in isolation. A future iteration will surface an explicit
 * doc-type select next to the attach button and replace this sniff.
 */
const _LAB_FILENAME_PATTERN = /\b(lab|panel|cbc|cmp|lipid|hba1c|results?)\b/i

export function inferDocType(filename: string): DocumentType {
  return _LAB_FILENAME_PATTERN.test(filename) ? 'lab_pdf' : 'intake_form'
}
