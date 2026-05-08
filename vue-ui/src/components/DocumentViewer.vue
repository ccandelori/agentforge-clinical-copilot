<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useTemplateRef, watch } from 'vue'

import { mapBBoxToPixels } from '@/components/DocumentViewer/mapBBoxToPixels'
import {
    loadPdfPages,
    type PdfLoader,
    type PdfPageRenderer,
    type PdfSource,
} from '@/components/DocumentViewer/pdfLoader'
import type { PageBBox } from '@/types/citation'

/**
 * DocumentViewer — renders a PDF page-by-page with normalized bounding
 * boxes overlaid on top. The "click-to-source" trust artifact for
 * intake-extracted fields and lab PDFs (T38.11).
 *
 * Architectural notes:
 *
 * - Bbox math lives in `mapBBoxToPixels` (pure, separately tested).
 * - PDF rendering is hidden behind the `PdfLoader` contract so unit
 *   tests can substitute an in-memory fake — booting PDF.js inside
 *   jsdom is unreliable and the load-bearing piece is overlay
 *   positioning, not pixel-level rendering.
 * - The viewer only consumes bboxes; it does not own a click handler.
 *   Wiring overlay clicks back to a citation pill is integration work
 *   that lives in the citations pane, per the T38.11 spec.
 */

interface Props {
    src: PdfSource
    bboxes: readonly PageBBox[]
    activeBBoxIndex?: number
    /**
     * Loader override. Default is the pdfjs-dist-backed `loadPdfPages`;
     * tests pass a stub. Untyped as required for tree-shake in case the
     * caller wants their own pipeline (e.g. server-rasterized images).
     */
    loader?: PdfLoader
}

const props = withDefaults(defineProps<Props>(), {
    activeBBoxIndex: undefined,
    loader: undefined,
})

interface RenderedPage {
    readonly index: number // 1-indexed, matches PageBBox.page
    readonly width: number
    readonly height: number
    readonly renderer: PdfPageRenderer
}

const pages = ref<readonly RenderedPage[]>([])
const loadError = ref<Error | null>(null)
const canvasRefs = useTemplateRef<HTMLCanvasElement[]>('canvasRefs')

/**
 * Index overlays per page so the template can do a single nested loop
 * without recomputing per-page filters on every render.
 */
interface IndexedBBox {
    readonly bbox: PageBBox
    readonly index: number // index into props.bboxes — survives filtering
}

const overlaysByPage = computed<ReadonlyMap<number, readonly IndexedBBox[]>>(() => {
    const out = new Map<number, IndexedBBox[]>()
    props.bboxes.forEach((bbox, index) => {
        const list = out.get(bbox.page) ?? []
        list.push({ bbox, index })
        out.set(bbox.page, list)
    })
    return out
})

function pageOverlays(pageIndex: number): readonly IndexedBBox[] {
    return overlaysByPage.value.get(pageIndex) ?? []
}

function isActive(index: number): boolean {
    return props.activeBBoxIndex === index
}

function overlayStyle(bbox: PageBBox, page: RenderedPage): string {
    const rect = mapBBoxToPixels(bbox, page.width, page.height)
    return [
        'position: absolute',
        `left: ${rect.left}px`,
        `top: ${rect.top}px`,
        `width: ${rect.width}px`,
        `height: ${rect.height}px`,
    ].join('; ')
}

async function loadDocument(): Promise<void> {
    loadError.value = null
    pages.value = []
    const load = props.loader ?? loadPdfPages
    try {
        const renderers = await load(props.src)
        pages.value = renderers.map((renderer, i) => ({
            index: i + 1,
            width: renderer.width,
            height: renderer.height,
            renderer,
        }))
        // Wait for Vue to render the v-for'd canvases before painting —
        // canvasRefs is empty until the template re-renders. Without
        // this, render() paints to a not-yet-mounted canvas and pages
        // come up blank-white (T38.16 live-test regression).
        await nextTick()
        await paintPages()
    } catch (caught) {
        loadError.value = caught instanceof Error ? caught : new Error('Failed to load PDF')
    }
}

async function paintPages(): Promise<void> {
    const canvases = canvasRefs.value ?? []
    await Promise.all(
        pages.value.map(async (page, i) => {
            const canvas = canvases[i]
            if (!(canvas instanceof HTMLCanvasElement)) return
            canvas.width = page.width
            canvas.height = page.height
            try {
                await page.renderer.render(canvas)
            } catch (caught) {
                // Per-page render failures shouldn't blank the whole
                // viewer — the overlay layer is still useful.
                if (caught instanceof Error) {
                    // eslint-disable-next-line no-console
                    console.warn('[DocumentViewer] page render failed', {
                        page: page.index,
                        error: caught.message,
                    })
                }
            }
        }),
    )
}

onMounted(() => {
    void loadDocument()
})

watch(
    () => props.src,
    () => {
        void loadDocument()
    },
)
</script>

<template>
    <div class="document-viewer">
        <div
            v-if="loadError !== null"
            data-testid="document-viewer-error"
            class="document-viewer__error"
            role="alert"
        >
            Could not load document.
        </div>
        <div
            v-for="page in pages"
            :key="page.index"
            class="document-viewer__page"
            :data-page="page.index"
            :style="`position: relative; width: ${page.width}px; height: ${page.height}px;`"
        >
            <canvas
                ref="canvasRefs"
                :width="page.width"
                :height="page.height"
                class="document-viewer__canvas"
            />
            <div
                v-for="overlay in pageOverlays(page.index)"
                :key="overlay.index"
                data-testid="bbox-overlay"
                :data-bbox-index="overlay.index"
                :data-active="isActive(overlay.index) ? 'true' : 'false'"
                :class="[
                    'document-viewer__overlay',
                    isActive(overlay.index) ? 'document-viewer__overlay--active' : '',
                ]"
                :style="overlayStyle(overlay.bbox, page)"
            />
        </div>
    </div>
</template>

<style scoped>
.document-viewer {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 1rem;
}

.document-viewer__page {
    margin-inline: auto;
    background: #fff;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}

.document-viewer__canvas {
    display: block;
}

.document-viewer__overlay {
    pointer-events: none;
    border: 2px solid #2563eb; /* blue-600 */
    background: rgba(37, 99, 235, 0.08);
    box-sizing: border-box;
    transition: border-color 120ms ease-out, background-color 120ms ease-out;
}

.document-viewer__overlay--active {
    border-color: #f59e0b; /* amber-500 */
    background: rgba(245, 158, 11, 0.18);
    box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.6);
}

.document-viewer__error {
    padding: 0.75rem 1rem;
    border: 1px solid #fecaca; /* red-200 */
    background: #fef2f2; /* red-50 */
    color: #991b1b; /* red-800 */
    border-radius: 0.375rem;
}
</style>
