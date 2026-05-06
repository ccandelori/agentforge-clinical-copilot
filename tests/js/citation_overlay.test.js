/**
 * Tests for citation_overlay.js (Task 24).
 *
 * Loads the IIFE into jsdom and exercises mount/unmount via a stubbed
 * pdfjsLib. The 1-indexed page contract test (T24.7's CRITICAL case) is
 * covered by asserting pdf.getPage(N) is called with the citation's
 * page_bbox.page directly — no off-by-one — which is the actual code
 * surface the contract bug would manifest at. Real PDF rendering isn't
 * exercised because jsdom can't paint to canvas; the contract is
 * verified at the API call boundary.
 *
 * @jest-environment jsdom
 */

'use strict';

const fs = require('fs');
const path = require('path');

const SCRIPT_PATH = path.resolve(
    __dirname,
    '../../interface/modules/custom_modules/oe-module-agentforge/public/js/citation_overlay.js'
);
const SCRIPT = fs.readFileSync(SCRIPT_PATH, 'utf8');

// jsdom returns 0 for offsetWidth/Height because there's no layout engine.
// Patch HTMLCanvasElement to surface the bitmap dimensions instead, so
// mountOverlayRect's pixel calculations are deterministic.
Object.defineProperty(HTMLCanvasElement.prototype, 'offsetWidth', {
    configurable: true,
    get: function () { return this.width; }
});
Object.defineProperty(HTMLCanvasElement.prototype, 'offsetHeight', {
    configurable: true,
    get: function () { return this.height; }
});

async function flush(rounds = 30) {
    for (let i = 0; i < rounds; i++) {
        await Promise.resolve();
    }
}

function makePdfjsLibMock(opts = {}) {
    const numPages = opts.numPages || 3;
    const pageCalls = [];
    const renderCalls = [];

    const mockPage = {
        getViewport: function ({ scale }) {
            return { width: 200 * scale, height: 300 * scale };
        },
        render: function (params) {
            renderCalls.push(params);
            return { promise: Promise.resolve() };
        }
    };

    const mockPdf = {
        numPages,
        getPage: function (n) {
            pageCalls.push(n);
            return Promise.resolve(mockPage);
        }
    };

    const lib = {
        GlobalWorkerOptions: { workerSrc: '' },
        getDocument: jest.fn(function () {
            return { promise: Promise.resolve(mockPdf) };
        })
    };

    return { lib, pageCalls, renderCalls };
}

function bbox(over = {}) {
    return Object.assign({
        page: 1,
        x0: 0.1,
        y0: 0.2,
        x1: 0.5,
        y1: 0.4
    }, over);
}

function loadOverlay() {
    // Re-evaluate the IIFE in this test's window so each test gets a
    // fresh window.AgentforgeCitationOverlay. Wrapping in `new Function`
    // runs in the global scope of the jsdom window, matching how it
    // would load via <script>.
    new Function(SCRIPT).call(window);
}

describe('citation_overlay.js', () => {
    let container;
    let warnSpy;
    let infoSpy;
    let errorSpy;

    beforeEach(() => {
        document.body.innerHTML = '<div id="container"></div>';
        container = document.getElementById('container');
        delete window.pdfjsLib;
        delete window.AgentforgeCitationOverlay;
        warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
        infoSpy = jest.spyOn(console, 'info').mockImplementation(() => {});
        errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
        loadOverlay();
    });

    afterEach(() => {
        warnSpy.mockRestore();
        infoSpy.mockRestore();
        errorSpy.mockRestore();
    });

    describe('public API', () => {
        it('exposes mount and unmount on window.AgentforgeCitationOverlay', () => {
            expect(typeof window.AgentforgeCitationOverlay).toBe('object');
            expect(typeof window.AgentforgeCitationOverlay.mount).toBe('function');
            expect(typeof window.AgentforgeCitationOverlay.unmount).toBe('function');
        });
    });

    describe('mount() validation', () => {
        it('warns and bails when container is missing', () => {
            window.AgentforgeCitationOverlay.mount(null, { page_bbox: bbox() }, '/test.pdf');
            expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('mount() requires'));
        });

        it('warns and bails when citation is missing', () => {
            window.AgentforgeCitationOverlay.mount(container, null, '/test.pdf');
            expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('mount() requires'));
        });

        it('warns and bails when pdfUrl is missing', () => {
            window.AgentforgeCitationOverlay.mount(container, { page_bbox: bbox() }, '');
            expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('mount() requires'));
        });

        it('renders an inline error when page_bbox is missing', () => {
            window.AgentforgeCitationOverlay.mount(container, {}, '/test.pdf');
            const err = container.querySelector('[data-role="overlay-error"]');
            expect(err).not.toBeNull();
            expect(err.textContent).toContain('page_bbox');
        });

        it('renders an inline error when page_bbox.page is non-numeric', () => {
            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: { page: '2', x0: 0, y0: 0, x1: 1, y1: 1 } },
                '/test.pdf'
            );
            const err = container.querySelector('[data-role="overlay-error"]');
            expect(err).not.toBeNull();
        });
    });

    describe('1-indexed page contract (CRITICAL)', () => {
        it('passes page_bbox.page=2 to pdf.getPage() unchanged — no off-by-one', async () => {
            const { lib, pageCalls } = makePdfjsLibMock({ numPages: 3 });
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: bbox({ page: 2 }) },
                '/test.pdf'
            );
            await flush();

            // The contract bug surfaces here: if mount() ever wraps the page
            // value with +1 or -1, this assertion fails.
            expect(pageCalls).toEqual([2]);
            expect(lib.getDocument).toHaveBeenCalledWith('/test.pdf');
        });

        it('passes page=1 through directly (no zero-conversion)', async () => {
            const { lib, pageCalls } = makePdfjsLibMock({ numPages: 3 });
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: bbox({ page: 1 }) },
                '/test.pdf'
            );
            await flush();

            expect(pageCalls).toEqual([1]);
        });
    });

    describe('out-of-range page', () => {
        it('shows an error when page > numPages', async () => {
            const { lib } = makePdfjsLibMock({ numPages: 3 });
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: bbox({ page: 5 }) },
                '/test.pdf'
            );
            await flush();

            const err = container.querySelector('[data-role="overlay-error"]');
            expect(err).not.toBeNull();
            expect(err.textContent).toContain('out of range');
        });

        it('shows an error when page < 1', async () => {
            const { lib } = makePdfjsLibMock({ numPages: 3 });
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: bbox({ page: 0 }) },
                '/test.pdf'
            );
            await flush();

            const err = container.querySelector('[data-role="overlay-error"]');
            expect(err).not.toBeNull();
        });
    });

    describe('overlay rect positioning', () => {
        it('positions rect from normalized bbox * canvas pixel dimensions', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;

            // canvas.width = 200 * 1.5 = 300, canvas.height = 300 * 1.5 = 450
            // bbox: x0=0.1 y0=0.2 x1=0.5 y1=0.4
            // expected: left=30, top=90, width=120 (0.4*300), height=90 (0.2*450)
            window.AgentforgeCitationOverlay.mount(
                container,
                { page_bbox: bbox({ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.4 }) },
                '/test.pdf'
            );
            await flush();

            const rect = container.querySelector('[data-role="overlay-rect"]');
            expect(rect).not.toBeNull();
            expect(rect.style.left).toBe('30px');
            expect(rect.style.top).toBe('90px');
            expect(rect.style.width).toBe('120px');
            expect(rect.style.height).toBe('90px');
        });

        it('sets container position: relative for absolute child positioning', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf'
            );
            await flush();

            expect(container.style.position).toBe('relative');
        });

        it('renders the rect with the spec\'s yellow-orange highlight styling', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf'
            );
            await flush();

            const rect = container.querySelector('[data-role="overlay-rect"]');
            expect(rect.style.background).toContain('255, 230, 0');
            expect(rect.style.border).toContain('255, 180, 0');
            expect(rect.style.zIndex).toBe('10');
            expect(rect.style.pointerEvents).toBe('auto');
            expect(rect.style.cursor).toBe('pointer');
        });
    });

    describe('dismiss handler', () => {
        it('removes the rect and fires onClose when the rect is clicked', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;
            const onClose = jest.fn();

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf', onClose
            );
            await flush();

            const rect = container.querySelector('[data-role="overlay-rect"]');
            expect(rect).not.toBeNull();
            rect.click();

            expect(container.querySelector('[data-role="overlay-rect"]')).toBeNull();
            expect(onClose).toHaveBeenCalledTimes(1);
        });

        it('removes the rect and fires onClose when the × button is clicked', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;
            const onClose = jest.fn();

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf', onClose
            );
            await flush();

            const closeBtn = container.querySelector('[data-role="overlay-close"]');
            expect(closeBtn).not.toBeNull();
            closeBtn.click();

            expect(container.querySelector('[data-role="overlay-rect"]')).toBeNull();
            expect(onClose).toHaveBeenCalledTimes(1);
        });

        it('does not double-fire onClose when × is clicked (event propagation stopped)', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;
            const onClose = jest.fn();

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf', onClose
            );
            await flush();

            const closeBtn = container.querySelector('[data-role="overlay-close"]');
            closeBtn.click();

            expect(onClose).toHaveBeenCalledTimes(1);
        });

        it('survives a missing onClose without throwing', async () => {
            const { lib } = makePdfjsLibMock();
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf'
            );
            await flush();

            const rect = container.querySelector('[data-role="overlay-rect"]');
            expect(() => rect.click()).not.toThrow();
        });
    });

    describe('whenPdfjsReady', () => {
        it('waits for the agentforge:pdfjs-ready event when pdfjsLib is not yet defined', async () => {
            // pdfjsLib starts undefined. mount() should not call getDocument yet.
            const { lib, pageCalls } = makePdfjsLibMock();

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox({ page: 2 }) }, '/test.pdf'
            );
            await flush();

            expect(pageCalls).toEqual([]);

            // Now simulate the panel template's deferred module script
            // finishing — set the global and dispatch the readiness event.
            window.pdfjsLib = lib;
            window.dispatchEvent(new CustomEvent('agentforge:pdfjs-ready'));
            await flush();

            expect(pageCalls).toEqual([2]);
        });
    });

    describe('error path', () => {
        it('shows an error and logs to console when getDocument rejects', async () => {
            const lib = {
                GlobalWorkerOptions: { workerSrc: '' },
                getDocument: jest.fn(function () {
                    return { promise: Promise.reject(new Error('boom')) };
                })
            };
            window.pdfjsLib = lib;

            window.AgentforgeCitationOverlay.mount(
                container, { page_bbox: bbox() }, '/test.pdf'
            );
            await flush();

            const err = container.querySelector('[data-role="overlay-error"]');
            expect(err).not.toBeNull();
            expect(err.textContent).toContain('boom');
            expect(errorSpy).toHaveBeenCalled();
        });
    });

    describe('unmount()', () => {
        it('clears all children of the container', () => {
            container.innerHTML = '<canvas></canvas><div></div>';
            window.AgentforgeCitationOverlay.unmount(container);
            expect(container.children.length).toBe(0);
        });

        it('is safe to call on a null container', () => {
            expect(() => window.AgentforgeCitationOverlay.unmount(null)).not.toThrow();
        });
    });
});
