/**
 * AgentForge Clinical Co-Pilot — citation overlay (Task 24).
 *
 * Vanilla-JS overlay component that renders a PDF page via pdf.js and
 * positions a highlight rectangle from a citation's normalized
 * `page_bbox` coordinates. Loaded as a classical <script> tag (IIFE)
 * alongside agent_panel.js. Calls into pdf.js via the `window.pdfjsLib`
 * global that the panel template's <script type="module"> sets up
 * after importing pdfjs-dist (5.x ESM legacy build). The worker URL
 * is configured by the same template script — this file does not
 * touch GlobalWorkerOptions.
 *
 * Public API (window.AgentforgeCitationOverlay):
 *   mount(container, citation, pdfUrl)
 *     Render the citation's PDF page into `container` and overlay a
 *     highlight rect on the cited region. `citation.page_bbox.page` is
 *     1-indexed per the W2_ARCHITECTURE.md PageBBox schema (and pdf.js
 *     itself uses 1-indexed pages — never subtract 1).
 *   unmount(container)
 *     Remove the rendered page and overlay; safe to call repeatedly.
 *
 * Subtask 24.3 establishes the IIFE skeleton + the pdfjsLib readiness
 * helper. Rendering (24.4), positioning (24.5), and styling (24.6)
 * land in subsequent commits.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */
(function () {
    'use strict';

    // pdf.js is loaded via a deferred <script type="module"> in the panel
    // template, so window.pdfjsLib may not exist yet when this IIFE runs.
    // whenPdfjsReady() resolves the moment the namespace is available —
    // either immediately (if the module script already finished) or via
    // the `agentforge:pdfjs-ready` event the template dispatches.
    function whenPdfjsReady(callback) {
        if (window.pdfjsLib) {
            callback(window.pdfjsLib);
            return;
        }
        window.addEventListener(
            'agentforge:pdfjs-ready',
            function () { callback(window.pdfjsLib); },
            { once: true }
        );
    }

    // Render scale. 1.5 produces a readable canvas without forcing the
    // browser to paint at full PDF resolution; the overlay rect sizes off
    // the resulting viewport so the scale only affects sharpness, not
    // positioning correctness.
    var RENDER_SCALE = 1.5;

    function unmount(container) {
        if (!container) {
            return;
        }
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }
    }

    // showError replaces container contents with a single element carrying
    // the message. Caller can pattern-match on `[data-role="overlay-error"]`
    // for testing.
    function showError(container, message) {
        unmount(container);
        var err = document.createElement('div');
        err.setAttribute('data-role', 'overlay-error');
        err.className = 'agentforge-citation-overlay-error text-danger small p-2';
        err.textContent = message;
        container.appendChild(err);
    }

    // renderPdfPage fetches the PDF, asks for the cited page, and paints
    // it onto a canvas appended to `container`. Returns a Promise that
    // resolves with { canvas, viewport } on success.
    //
    // CRITICAL: citation.page_bbox.page is 1-indexed per W2_ARCHITECTURE.md
    // §2.2 (PageBBox.page: int = Field(ge=1)). pdf.js getPage() is also
    // 1-indexed. Pass the value through directly — never subtract 1.
    function renderPdfPage(pdfjsLib, container, pageNumber, pdfUrl) {
        var loadingTask = pdfjsLib.getDocument(pdfUrl);
        return loadingTask.promise.then(function (pdf) {
            if (pageNumber < 1 || pageNumber > pdf.numPages) {
                throw new Error(
                    'Citation page ' + pageNumber + ' out of range (PDF has '
                    + pdf.numPages + ' pages)'
                );
            }
            return pdf.getPage(pageNumber);
        }).then(function (page) {
            var viewport = page.getViewport({ scale: RENDER_SCALE });
            var canvas = document.createElement('canvas');
            canvas.setAttribute('data-role', 'overlay-canvas');
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            container.appendChild(canvas);
            var ctx = canvas.getContext('2d');
            return page.render({
                canvasContext: ctx,
                viewport: viewport
            }).promise.then(function () {
                return { canvas: canvas, viewport: viewport };
            });
        });
    }

    function mount(container, citation, pdfUrl) {
        if (!container || !citation || !pdfUrl) {
            console.warn(
                '[AgentforgeCitationOverlay] mount() requires container, citation, pdfUrl'
            );
            return;
        }
        var pageBbox = citation.page_bbox;
        if (!pageBbox || typeof pageBbox.page !== 'number') {
            showError(container, 'Citation is missing page_bbox.page');
            return;
        }
        // Container hosts an absolutely-positioned overlay rect (24.5+),
        // so it must establish a positioning context.
        unmount(container);
        container.style.position = 'relative';

        whenPdfjsReady(function (pdfjsLib) {
            renderPdfPage(pdfjsLib, container, pageBbox.page, pdfUrl)
                .then(function (rendered) {
                    mountOverlayRect(container, rendered.canvas, pageBbox);
                })
                .catch(function (err) {
                    console.error(
                        '[AgentforgeCitationOverlay] render failed', err
                    );
                    showError(
                        container,
                        'Could not render citation: ' + (err.message || err)
                    );
                });
        });
    }

    // mountOverlayRect creates an absolutely-positioned <div> over the
    // rendered canvas, sized from the citation's normalized 0..1 bbox.
    // Sourcing dimensions from canvas.offsetWidth/Height (rather than the
    // viewport bitmap dimensions) means CSS rescaling of the canvas — e.g.,
    // max-width on a parent — flows through to the overlay automatically.
    function mountOverlayRect(container, canvas, pageBbox) {
        var width = canvas.offsetWidth;
        var height = canvas.offsetHeight;
        var rect = document.createElement('div');
        rect.setAttribute('data-role', 'overlay-rect');
        rect.style.position = 'absolute';
        rect.style.left = (pageBbox.x0 * width) + 'px';
        rect.style.top = (pageBbox.y0 * height) + 'px';
        rect.style.width = ((pageBbox.x1 - pageBbox.x0) * width) + 'px';
        rect.style.height = ((pageBbox.y1 - pageBbox.y0) * height) + 'px';
        container.appendChild(rect);
        return rect;
    }

    window.AgentforgeCitationOverlay = {
        mount: mount,
        unmount: unmount
    };
})();
