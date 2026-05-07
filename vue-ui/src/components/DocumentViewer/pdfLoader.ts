/**
 * PDF loading abstraction for DocumentViewer.
 *
 * The component takes a `loader` prop typed as `PdfLoader` so the spec
 * can substitute an in-memory fake without booting PDF.js inside jsdom
 * (PDF.js's worker + canvas pipeline is a notorious source of test
 * flake — see T38.11 deviation log if/when it bites).
 *
 * The default loader, `loadPdfPages`, dynamically imports `pdfjs-dist`
 * the first time it runs. The dynamic import keeps the worker setup off
 * the synchronous module-eval path so the unit test never pulls
 * `pdfjs-dist` into the dependency graph.
 */

/**
 * Source the loader knows how to ingest. Strings are treated as URLs;
 * Blob/ArrayBuffer go through PDF.js's data path.
 */
export type PdfSource = string | Blob | ArrayBuffer

/**
 * Renderable page: dimensions in CSS pixels at the chosen scale, plus a
 * render function the component invokes once it has a canvas mounted.
 *
 * `width` and `height` MUST match the canvas the renderer paints into;
 * `mapBBoxToPixels` consumes these values directly.
 */
export interface PdfPageRenderer {
    readonly width: number
    readonly height: number
    /**
     * Paint the page into the supplied canvas's 2D context. The canvas's
     * `width`/`height` are expected to already match the renderer's
     * dimensions when called.
     */
    render(canvas: HTMLCanvasElement): Promise<void>
}

/**
 * Loader contract. Returns one renderer per PDF page in 1-indexed
 * order (i.e. `pages[0]` is page 1).
 */
export type PdfLoader = (src: PdfSource) => Promise<readonly PdfPageRenderer[]>

const DEFAULT_RENDER_SCALE = 1.5

/**
 * Default loader backed by pdfjs-dist. Dynamic import avoids pulling
 * the worker into the test bundle and lets the SPA tree-shake it from
 * routes that don't use the viewer.
 */
export const loadPdfPages: PdfLoader = async (src) => {
    type PdfDocumentProxy = {
        readonly numPages: number
        getPage: (n: number) => Promise<PdfPageProxy>
    }
    type PdfViewport = { readonly width: number; readonly height: number }
    type RenderTask = { promise: Promise<void> }
    type PdfPageProxy = {
        getViewport: (params: { scale: number }) => PdfViewport
        render: (params: {
            canvasContext: CanvasRenderingContext2D
            viewport: PdfViewport
        }) => RenderTask
    }
    type GetDocumentParams =
        | { url: string }
        | { data: ArrayBuffer | Uint8Array }

    const pdfjs = (await import('pdfjs-dist')) as unknown as {
        getDocument: (params: GetDocumentParams) => { promise: Promise<PdfDocumentProxy> }
        GlobalWorkerOptions: { workerSrc: string }
        version: string
    }

    // Wire the worker to the matching version on the public CDN. Hosting
    // our own copy is a follow-up — the production cutover (T38.14)
    // serves vue-ui from the sidecar so we'll bundle the worker there.
    if (!pdfjs.GlobalWorkerOptions.workerSrc) {
        pdfjs.GlobalWorkerOptions.workerSrc =
            `https://cdn.jsdelivr.net/npm/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`
    }

    const params = await toGetDocumentParams(src)
    const doc = await pdfjs.getDocument(params).promise

    const pages: PdfPageRenderer[] = []
    for (let i = 1; i <= doc.numPages; i += 1) {
        const page = await doc.getPage(i)
        const viewport = page.getViewport({ scale: DEFAULT_RENDER_SCALE })
        pages.push({
            width: viewport.width,
            height: viewport.height,
            async render(canvas: HTMLCanvasElement): Promise<void> {
                const ctx = canvas.getContext('2d')
                if (ctx === null) {
                    throw new Error('canvas 2D context unavailable')
                }
                await page.render({ canvasContext: ctx, viewport }).promise
            },
        })
    }
    return pages
}

async function toGetDocumentParams(
    src: PdfSource,
): Promise<{ url: string } | { data: ArrayBuffer | Uint8Array }> {
    if (typeof src === 'string') {
        return { url: src }
    }
    if (src instanceof ArrayBuffer) {
        return { data: src }
    }
    // Blob → ArrayBuffer
    const buffer = await src.arrayBuffer()
    return { data: buffer }
}
