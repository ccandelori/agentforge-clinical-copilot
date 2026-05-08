import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import DocumentViewer from '@/components/DocumentViewer.vue'
import type { PdfPageRenderer } from '@/components/DocumentViewer/pdfLoader'
import type { PageBBox } from '@/types/citation'

/**
 * Build a stub PdfPageRenderer for a page of the given dimensions. The
 * component never asks the page to actually paint pixels — overlay
 * positioning is the load-bearing thing — so render() is a no-op spy
 * that records calls.
 */
function fakePage(width: number, height: number): PdfPageRenderer {
    return {
        width,
        height,
        render: vi.fn(async () => {}),
    }
}

function bbox(
    page: number,
    x0: number,
    y0: number,
    x1: number,
    y1: number,
    confidence = 0.9,
): PageBBox {
    return { page, x0, y0, x1, y1, bbox_confidence: confidence }
}

describe('DocumentViewer', () => {
    it('renders one overlay per bbox at the expected pixel position', async () => {
        const loader = vi.fn(async () => [fakePage(1000, 1500)])

        const wrapper = mount(DocumentViewer, {
            props: {
                src: 'fixture://demo.pdf',
                bboxes: [bbox(1, 0, 0, 0.5, 0.5)],
                loader,
            },
        })

        await flushPromises()

        const overlays = wrapper.findAll('[data-testid="bbox-overlay"]')
        expect(overlays).toHaveLength(1)

        const style = overlays[0]!.attributes('style') ?? ''
        expect(style).toContain('left: 0px')
        expect(style).toContain('top: 0px')
        expect(style).toContain('width: 500px')
        expect(style).toContain('height: 750px')
    })

    it('only renders overlays for bboxes whose page exists', async () => {
        const loader = vi.fn(async () => [fakePage(800, 600)])

        const wrapper = mount(DocumentViewer, {
            props: {
                src: 'fixture://demo.pdf',
                bboxes: [
                    bbox(1, 0.1, 0.2, 0.4, 0.7),
                    // page 2 is out of range — must be silently skipped
                    bbox(2, 0, 0, 1, 1),
                ],
                loader,
            },
        })

        await flushPromises()

        const overlays = wrapper.findAll('[data-testid="bbox-overlay"]')
        expect(overlays).toHaveLength(1)
    })

    it('marks the active bbox with the data-active attribute', async () => {
        const loader = vi.fn(async () => [fakePage(1000, 1000)])

        const wrapper = mount(DocumentViewer, {
            props: {
                src: 'fixture://demo.pdf',
                bboxes: [
                    bbox(1, 0, 0, 0.5, 0.5),
                    bbox(1, 0.5, 0.5, 1, 1),
                ],
                activeBBoxIndex: 1,
                loader,
            },
        })

        await flushPromises()

        const overlays = wrapper.findAll('[data-testid="bbox-overlay"]')
        expect(overlays).toHaveLength(2)
        expect(overlays[0]!.attributes('data-active')).toBe('false')
        expect(overlays[1]!.attributes('data-active')).toBe('true')
    })

    it('surfaces a load error without throwing', async () => {
        const loader = vi.fn(async () => {
            throw new Error('boom')
        })

        const wrapper = mount(DocumentViewer, {
            props: {
                src: 'fixture://broken.pdf',
                bboxes: [],
                loader,
            },
        })

        await flushPromises()

        // The component should display some error state and render zero
        // pages / overlays rather than blowing up the parent tree.
        expect(wrapper.findAll('[data-testid="bbox-overlay"]')).toHaveLength(0)
        expect(wrapper.find('[data-testid="document-viewer-error"]').exists()).toBe(true)
    })
})
