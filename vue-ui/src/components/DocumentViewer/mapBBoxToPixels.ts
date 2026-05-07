/**
 * Pure geometry helper for the DocumentViewer overlay layer.
 *
 * Lives in its own module so the spec can test it without mounting the
 * Vue component (PDF.js + jsdom is a noisy combination — see
 * `DocumentViewer.spec.ts` for the rationale). The component itself
 * imports this directly and never duplicates the math.
 *
 * @see PageBBox in `@/types/citation`
 * @see T38.11
 */

import type { PageBBox } from '@/types/citation'

/**
 * Pixel-space rectangle suitable for inline-style positioning of an
 * absolutely-positioned overlay div (`left/top/width/height` in px).
 *
 * Values are intentionally not rounded — rounding belongs at the
 * rendering boundary so the consumer can decide between `Math.round`
 * (sharp 1px borders) and sub-pixel rendering (smoother on HiDPI).
 */
export interface PixelRect {
    readonly left: number
    readonly top: number
    readonly width: number
    readonly height: number
}

/**
 * Convert a normalized `PageBBox` (0..1, top-left origin) into a pixel
 * rectangle on a page rendered at `pageWidth` x `pageHeight`.
 *
 * The PDF.js viewport this is paired with also uses a top-left origin
 * once `viewport.transform` has been applied (the default for canvas
 * rendering), so the math is a straight scale — no axis flip needed.
 *
 * Degenerate or inverted boxes are passed through (the schema rejects
 * them upstream); the resulting `width`/`height` may be `0` or negative
 * but the helper never throws or produces `NaN` for finite inputs.
 */
export function mapBBoxToPixels(
    bbox: PageBBox,
    pageWidth: number,
    pageHeight: number,
): PixelRect {
    const left = bbox.x0 * pageWidth
    const top = bbox.y0 * pageHeight
    const width = (bbox.x1 - bbox.x0) * pageWidth
    const height = (bbox.y1 - bbox.y0) * pageHeight
    return { left, top, width, height }
}
