import { describe, expect, it } from 'vitest'

import { mapBBoxToPixels } from '@/components/DocumentViewer/mapBBoxToPixels'
import type { PageBBox } from '@/types/citation'

function bbox(
    x0: number,
    y0: number,
    x1: number,
    y1: number,
    confidence = 0.9,
): PageBBox {
    return { page: 1, x0, y0, x1, y1, bbox_confidence: confidence }
}

describe('mapBBoxToPixels', () => {
    it('maps a full-page box to the page rectangle', () => {
        const result = mapBBoxToPixels(bbox(0, 0, 1, 1), 1000, 1500)
        expect(result).toEqual({ left: 0, top: 0, width: 1000, height: 1500 })
    })

    it('maps the top-left quadrant on a 1000x1500 page', () => {
        const result = mapBBoxToPixels(bbox(0, 0, 0.5, 0.5), 1000, 1500)
        expect(result).toEqual({ left: 0, top: 0, width: 500, height: 750 })
    })

    it('maps the bottom-right quadrant on a 1000x1500 page', () => {
        const result = mapBBoxToPixels(bbox(0.5, 0.5, 1, 1), 1000, 1500)
        expect(result).toEqual({ left: 500, top: 750, width: 500, height: 750 })
    })

    it('maps an asymmetric box on an 800x600 page', () => {
        // Floating-point: (0.4 - 0.1) * 800 lands at 240.0000…03. The
        // helper deliberately doesn't pre-round (see PixelRect docs);
        // assert numeric equality, not bit-exact identity.
        const result = mapBBoxToPixels(bbox(0.1, 0.2, 0.4, 0.7), 800, 600)
        expect(result.left).toBe(80)
        expect(result.top).toBe(120)
        expect(result.width).toBeCloseTo(240, 9)
        expect(result.height).toBeCloseTo(300, 9)
    })

    it('produces zero-area output for a degenerate box (defence-in-depth)', () => {
        // The sidecar schema rejects x1 <= x0 / y1 <= y0, but the helper
        // must not divide-by-zero or NaN if a malformed bbox slips through.
        const result = mapBBoxToPixels(bbox(0.3, 0.3, 0.3, 0.3), 1000, 1000)
        expect(result).toEqual({ left: 300, top: 300, width: 0, height: 0 })
    })

    it('preserves fractional pixels (no rounding)', () => {
        // Rounding belongs at the rendering boundary, not the math layer.
        // Round-tripping a 1/3 normalized box on a 1000px page should land
        // on 333.333… so callers can round consistently.
        const result = mapBBoxToPixels(bbox(0, 0, 1 / 3, 1 / 3), 1000, 1000)
        expect(result.left).toBe(0)
        expect(result.top).toBe(0)
        expect(result.width).toBeCloseTo(333.333, 2)
        expect(result.height).toBeCloseTo(333.333, 2)
    })
})
