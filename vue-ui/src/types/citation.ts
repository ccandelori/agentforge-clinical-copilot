/**
 * Shared citation geometry types used by the AgentForge drawer + document
 * viewer.
 *
 * Mirrors the sidecar contract in
 * `sidecar/src/agentforge/schemas/citation.py` (T38.11). The component-side
 * shape stays structural so the drawer can pass through whatever the
 * sidecar emits without an extra translation layer; the runtime guarantees
 * that scanned-source bboxes carry `bbox_confidence >= 0.7` are enforced
 * upstream by the Pydantic schema.
 */

/**
 * Normalized 0..1 bounding box on a 1-indexed PDF page.
 *
 * Coordinates use a top-left origin (matches PDF.js's default viewport
 * orientation and `mapBBoxToPixels` math). Inverted or zero-area boxes
 * (`x1 <= x0` or `y1 <= y0`) are rejected at the sidecar boundary, so
 * downstream code may assume `x1 > x0` and `y1 > y0`.
 */
export interface PageBBox {
    /** 1-indexed PDF page number; `1` maps to the first rendered page. */
    readonly page: number
    /** Normalized 0..1 left edge. */
    readonly x0: number
    /** Normalized 0..1 top edge (top-left origin). */
    readonly y0: number
    /** Normalized 0..1 right edge; must be strictly greater than `x0`. */
    readonly x1: number
    /** Normalized 0..1 bottom edge; must be strictly greater than `y0`. */
    readonly y1: number
    /**
     * VLM-reported confidence in the geometric box, 0..1. Schema floor is
     * 0.7 for scanned-source citations (`LAB_PDF`, `INTAKE_FORM`); lower
     * values are dropped to `unsupported_fields` server-side and never
     * reach the viewer.
     */
    readonly bbox_confidence: number
}
