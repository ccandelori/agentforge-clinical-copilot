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

    function unmount(container) {
        if (!container) {
            return;
        }
        while (container.firstChild) {
            container.removeChild(container.firstChild);
        }
    }

    // Implementation lands in 24.4 (rendering), 24.5 (positioning), 24.6
    // (styling + dismiss). For now mount() validates inputs and surfaces
    // a console warning so an early caller doesn't fail silently.
    function mount(container, citation, pdfUrl) {
        if (!container || !citation || !pdfUrl) {
            console.warn(
                '[AgentforgeCitationOverlay] mount() requires container, citation, pdfUrl'
            );
            return;
        }
        whenPdfjsReady(function (/* pdfjsLib */) {
            // 24.4+ — render citation.page_bbox.page from pdfUrl into
            // container, then overlay a positioned highlight rect.
            console.info(
                '[AgentforgeCitationOverlay] mount() pending implementation (T24.4+)',
                { citation: citation, pdfUrl: pdfUrl }
            );
        });
    }

    window.AgentforgeCitationOverlay = {
        mount: mount,
        unmount: unmount
    };
})();
