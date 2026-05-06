/**
 * Tests for agent_panel.js citation rendering (Task 24 integration).
 *
 * Exercises the citation chip + viewer surface that appears beneath
 * agent bubbles when a /turn response carries an extraction with bbox
 * citations. The citation overlay itself (mount/unmount) is stubbed —
 * its own behavior is covered by tests/js/citation_overlay.test.js.
 *
 * @jest-environment jsdom
 */

'use strict';

const fs = require('fs');
const path = require('path');

if (typeof TextDecoder === 'undefined') {
    const { TextDecoder: TD, TextEncoder: TE } = require('util');
    global.TextDecoder = TD;
    global.TextEncoder = TE;
}

const SCRIPT = fs.readFileSync(
    path.resolve(
        __dirname,
        '../../interface/modules/custom_modules/oe-module-agentforge/public/js/agent_panel.js'
    ),
    'utf8'
);

async function flush(rounds = 30) {
    for (let i = 0; i < rounds; i++) {
        await Promise.resolve();
    }
}

function buildPanel() {
    document.body.innerHTML = `
        <div class="agentforge-panel" data-turn-url="/agentforge/turn">
            <form data-role="form">
                <input data-role="input" type="text" value="">
                <button data-role="send" type="submit">Send</button>
            </form>
            <div data-role="messages">
                <div data-role="empty-state">Ask a question.</div>
            </div>
            <div data-role="status" hidden></div>
        </div>
    `;
    // eslint-disable-next-line no-eval
    eval(SCRIPT);
    return document.querySelector('.agentforge-panel');
}

function submitMessage(panel, text) {
    const input = panel.querySelector('[data-role="input"]');
    const form = panel.querySelector('[data-role="form"]');
    input.value = text;
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

function jsonResponse(payload) {
    return {
        ok: true,
        status: 200,
        headers: { get: (n) => n.toLowerCase() === 'content-type' ? 'application/json' : null },
        text: () => Promise.resolve(JSON.stringify(payload)),
    };
}

function bbox(over = {}) {
    return Object.assign({
        page: 1,
        x0: 0.1,
        y0: 0.1,
        x1: 0.5,
        y1: 0.3,
        bbox_confidence: 0.9
    }, over);
}

function citation(over = {}) {
    return Object.assign({
        source_type: 'intake_form',
        source_id: '8',
        page_or_section: 'page 1',
        field_or_chunk_id: 'demographics[0].value',
        quote_or_value: 'Patient name: Susan Smith',
        page_bbox: bbox()
    }, over);
}

const BASE_EXTRACTION = {
    document_id: 8,
    patient_id: 42,
    chief_concern: 'Knee pain',
    chief_concern_citation: citation({
        field_or_chunk_id: 'chief_concern',
        quote_or_value: 'Knee pain for 3 weeks'
    }),
    demographics: [
        { field: 'date_of_birth', value: '1972-04-12', citation: citation({
            field_or_chunk_id: 'demographics[0]',
            quote_or_value: 'DOB: 04/12/1972',
            page_bbox: bbox({ page: 2 })
        }) }
    ],
    medications: [],
    allergies: [],
    family_history: [],
    extraction_confidence: 0.85,
    unsupported_fields: []
};

describe('agent_panel citation integration', () => {
    let panel;
    let mountSpy;
    let unmountSpy;

    beforeEach(() => {
        jest.clearAllMocks();
        panel = buildPanel();
        mountSpy = jest.fn();
        unmountSpy = jest.fn();
        window.AgentforgeCitationOverlay = {
            mount: mountSpy,
            unmount: unmountSpy
        };
    });

    afterEach(() => {
        delete window.AgentforgeCitationOverlay;
    });

    test('renders a chip per bbox-bearing citation in the extraction', async () => {
        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: BASE_EXTRACTION
        }));

        submitMessage(panel, 'extract this');
        await flush();

        const chips = document.querySelectorAll('[data-role="citation-chip"]');
        expect(chips.length).toBe(2); // chief_concern + demographics[0]
    });

    test('skips citations without page_bbox (e.g. guideline citations)', async () => {
        const extraction = Object.assign({}, BASE_EXTRACTION, {
            demographics: [
                { field: 'dob', value: '1972', citation: {
                    source_type: 'guideline',
                    source_id: 'g-1',
                    page_or_section: 'Section 4.1',
                    field_or_chunk_id: 'demographics[0]',
                    quote_or_value: 'whatever',
                    page_bbox: null
                } }
            ]
        });

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: extraction
        }));

        submitMessage(panel, 'extract');
        await flush();

        // Only chief_concern_citation has a bbox; demographics' guideline
        // citation should be skipped.
        const chips = document.querySelectorAll('[data-role="citation-chip"]');
        expect(chips.length).toBe(1);
    });

    test('does not render the panel when extraction has no citations', async () => {
        const extraction = Object.assign({}, BASE_EXTRACTION, {
            chief_concern: null,
            chief_concern_citation: null,
            demographics: []
        });

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Nothing to extract.',
            extraction: extraction
        }));

        submitMessage(panel, 'extract');
        await flush();

        expect(document.querySelector('[data-role="citations-panel"]')).toBeNull();
    });

    test('does not render the panel when patient_id is missing', async () => {
        const extraction = Object.assign({}, BASE_EXTRACTION, {
            patient_id: null
        });

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Hmm.',
            extraction: extraction
        }));

        submitMessage(panel, 'extract');
        await flush();

        expect(document.querySelector('[data-role="citations-panel"]')).toBeNull();
    });

    test('does not render the panel for chart-question turns (extraction null)', async () => {
        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Chart answer.',
            extraction: null
        }));

        submitMessage(panel, 'recent labs?');
        await flush();

        expect(document.querySelector('[data-role="citations-panel"]')).toBeNull();
    });

    test('chip click calls window.AgentforgeCitationOverlay.mount with the right args', async () => {
        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: BASE_EXTRACTION
        }));

        submitMessage(panel, 'extract');
        await flush();

        const chips = document.querySelectorAll('[data-role="citation-chip"]');
        expect(chips.length).toBe(2);
        chips[0].click();

        expect(mountSpy).toHaveBeenCalledTimes(1);
        const [container, citationArg, pdfUrl, onClose] = mountSpy.mock.calls[0];
        expect(container).toBe(document.querySelector('[data-role="citation-viewer"]'));
        expect(citationArg.field_or_chunk_id).toBe('chief_concern');
        // patient_id=42 from BASE_EXTRACTION; document_id=8 from citation.source_id.
        expect(pdfUrl).toBe(
            '/controller.php?document&retrieve&patient_id=42&document_id=8&as_file=false'
        );
        expect(typeof onClose).toBe('function');
    });

    test('onClose callback unmounts the viewer container', async () => {
        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: BASE_EXTRACTION
        }));

        submitMessage(panel, 'extract');
        await flush();

        document.querySelector('[data-role="citation-chip"]').click();
        const onClose = mountSpy.mock.calls[0][3];
        onClose();

        expect(unmountSpy).toHaveBeenCalledTimes(1);
        expect(unmountSpy.mock.calls[0][0]).toBe(
            document.querySelector('[data-role="citation-viewer"]')
        );
    });

    test('chip label uses field_or_chunk_id and a truncated quote', async () => {
        const longQuote = 'a'.repeat(50);
        const extraction = Object.assign({}, BASE_EXTRACTION, {
            chief_concern_citation: citation({
                field_or_chunk_id: 'chief_concern',
                quote_or_value: longQuote
            }),
            demographics: []
        });

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: extraction
        }));

        submitMessage(panel, 'extract');
        await flush();

        const chip = document.querySelector('[data-role="citation-chip"]');
        // field_or_chunk_id + ': ' + 30 chars of quote + '…'
        expect(chip.textContent.startsWith('chief_concern: ')).toBe(true);
        expect(chip.textContent).toContain('…');
        // The native title attribute carries the full quote for hover.
        expect(chip.title).toBe(longQuote);
    });

    test('warns if AgentforgeCitationOverlay is not loaded when chip is clicked', async () => {
        delete window.AgentforgeCitationOverlay;
        const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: BASE_EXTRACTION
        }));

        submitMessage(panel, 'extract');
        await flush();

        document.querySelector('[data-role="citation-chip"]').click();
        expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining('citation overlay not loaded')
        );
        warnSpy.mockRestore();
    });

    test('walks the page_bbox.page through to the citation passed to mount', async () => {
        // Multi-page citation — verifies the integration preserves the
        // 1-indexed page contract end-to-end.
        const extraction = Object.assign({}, BASE_EXTRACTION, {
            chief_concern_citation: citation({
                field_or_chunk_id: 'chief_concern',
                page_bbox: bbox({ page: 3 })
            }),
            demographics: []
        });

        global.fetch = jest.fn().mockResolvedValue(jsonResponse({
            reply: 'Extracted.',
            extraction: extraction
        }));

        submitMessage(panel, 'extract');
        await flush();

        document.querySelector('[data-role="citation-chip"]').click();
        const citationArg = mountSpy.mock.calls[0][1];
        expect(citationArg.page_bbox.page).toBe(3);
    });
});
