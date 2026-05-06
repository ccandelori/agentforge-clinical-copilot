/**
 * Tests for agent_panel.js W2 upload + send wiring (MR 7 slice E).
 *
 * The chat panel grows a file-input + "Attach intake form" button so a
 * user can upload a PDF, then send a chat message that triggers
 * extraction in the W2 LangGraph. The JS must:
 *
 *   * POST the file as multipart/form-data to ``data-upload-url`` with
 *     a ``doc_type=intake_form`` field
 *   * On HTTP 201, stash the returned ``document_id`` on the panel
 *     dataset and update an indicator element
 *   * On the next form submit, include ``document_id`` in the /turn
 *     request body and clear the pending state so a follow-up
 *     message doesn't re-attach the same document
 *   * If a "Search guidelines" toggle is checked, also include
 *     ``evidence_query`` set to the message text
 *   * Stay backward-compatible with panels that don't render the
 *     new widgets (existing chart-question deployments)
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

// OpenEMR exposes the per-session CSRF token as ``top.csrf_token_js``
// from interface/main/tabs/main.php. Multipart POSTs to
// upload_document.php require it as a ``csrf_token_form`` field —
// matching the existing pattern in library/js/dwv/dicom_gui.js. The
// jsdom harness has no top frame, so we set the global directly.
function setTopCsrfToken(token) {
    // ``top`` is the same Window in jsdom; assigning the property
    // mirrors what production gets from main.php's inline script.
    top.csrf_token_js = token;
}

// Panel HTML mirrors the post-MR-7 agent_panel.html.twig: chat surface
// plus an upload row with a file input, button, and pending-doc badge,
// plus a "Search guidelines" toggle.
function buildPanelWithUpload() {
    document.body.innerHTML = `
        <div class="agentforge-panel"
             data-turn-url="/agentforge/turn"
             data-upload-url="/agentforge/upload_document">
            <div data-role="messages">
                <div data-role="empty-state">Ask a question.</div>
            </div>
            <div class="agentforge-upload-row">
                <input type="file" data-role="upload-input" hidden accept="application/pdf">
                <button type="button" data-role="upload-button">Attach intake form</button>
                <span data-role="pending-doc-indicator" hidden></span>
                <label>
                    <input type="checkbox" data-role="guidelines-toggle">
                    Search guidelines
                </label>
            </div>
            <form data-role="form">
                <input data-role="input" type="text" value="">
                <button data-role="send" type="submit">Send</button>
            </form>
            <div data-role="status" hidden></div>
        </div>
    `;
    // eslint-disable-next-line no-eval
    eval(SCRIPT);
    return document.querySelector('.agentforge-panel');
}

// Panel HTML matching the pre-MR-7 layout — no upload widgets, no
// guidelines toggle. Used for the regression-lock test that the JS
// degrades gracefully when these elements are absent.
function buildPanelWithoutUpload() {
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

function pickPdfFile(panel, content = '%PDF-1.4 stub') {
    const input = panel.querySelector('[data-role="upload-input"]');
    const file = new File([content], 'intake.pdf', { type: 'application/pdf' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return file;
}

function submitMessage(panel, text) {
    const input = panel.querySelector('[data-role="input"]');
    const form = panel.querySelector('[data-role="form"]');
    input.value = text;
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}


describe('agent_panel upload widget', () => {
    let panel;

    beforeEach(() => {
        jest.clearAllMocks();
        panel = buildPanelWithUpload();
    });

    test('clicking upload button opens the hidden file input', () => {
        const fileInput = panel.querySelector('[data-role="upload-input"]');
        const clickSpy = jest.spyOn(fileInput, 'click');

        panel.querySelector('[data-role="upload-button"]').click();

        expect(clickSpy).toHaveBeenCalledTimes(1);
    });

    test('selecting a file POSTs multipart to upload-url with doc_type=intake_form and CSRF token', async () => {
        // upload_document.php's CsrfUtils::verifyCsrfToken check rejects
        // any request whose ``csrf_token_form`` field doesn't match the
        // active OpenEMR session. The token rides as a multipart field,
        // not a header — same shape as library/js/dwv/dicom_gui.js does.
        setTopCsrfToken('test-csrf-token');

        let capturedUrl = '';
        let capturedBody = null;
        global.fetch = jest.fn().mockImplementation((url, opts) => {
            capturedUrl = url;
            capturedBody = opts.body;
            return Promise.resolve({
                ok: true,
                status: 201,
                headers: { get: () => 'application/json' },
                text: () => Promise.resolve('{"success":true,"document_id":42}'),
            });
        });

        pickPdfFile(panel);
        await flush();

        expect(capturedUrl).toBe('/agentforge/upload_document');
        expect(capturedBody).toBeInstanceOf(FormData);
        expect(capturedBody.get('doc_type')).toBe('intake_form');
        expect(capturedBody.get('csrf_token_form')).toBe('test-csrf-token');
        const file = capturedBody.get('file');
        expect(file).toBeInstanceOf(File);
        expect(file.name).toBe('intake.pdf');
    });

    test('successful upload stashes document_id on panel dataset', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            status: 201,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"success":true,"document_id":42}'),
        });

        pickPdfFile(panel);
        await flush();

        expect(panel.dataset.pendingDocumentId).toBe('42');
        const indicator = panel.querySelector('[data-role="pending-doc-indicator"]');
        expect(indicator.hidden).toBe(false);
        expect(indicator.textContent).toContain('42');
    });

    test('upload failure surfaces an error bubble and leaves dataset empty', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 400,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"error":"not a PDF"}'),
        });

        pickPdfFile(panel);
        await flush();

        expect(panel.dataset.pendingDocumentId || '').toBe('');
        const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
        expect(msgs.length).toBe(1);
        expect(msgs[0].querySelector('div').textContent).toMatch(/not a PDF|400/);
    });
});


describe('agent_panel send with W2 inputs', () => {
    let panel;

    beforeEach(() => {
        jest.clearAllMocks();
        panel = buildPanelWithUpload();
    });

    test('next send after upload includes document_id and clears the pending state', async () => {
        // First call = upload; second call = /turn.
        const calls = [];
        global.fetch = jest.fn().mockImplementation((url, opts) => {
            calls.push({ url, body: opts.body, headers: opts.headers });
            if (url === '/agentforge/upload_document') {
                return Promise.resolve({
                    ok: true,
                    status: 201,
                    headers: { get: () => 'application/json' },
                    text: () => Promise.resolve('{"success":true,"document_id":42}'),
                });
            }
            return Promise.resolve({
                ok: true,
                headers: { get: () => 'application/json' },
                text: () => Promise.resolve('{"reply":"extracted"}'),
            });
        });

        pickPdfFile(panel);
        await flush();
        submitMessage(panel, 'extract this intake form');
        await flush();

        const turnCall = calls.find(c => c.url === '/agentforge/turn');
        expect(turnCall).toBeDefined();
        const body = JSON.parse(turnCall.body);
        expect(body.message).toBe('extract this intake form');
        expect(body.document_id).toBe(42);

        // The pending state clears the moment the send fires so a
        // follow-up message doesn't re-attach the same document.
        expect(panel.dataset.pendingDocumentId || '').toBe('');
        const indicator = panel.querySelector('[data-role="pending-doc-indicator"]');
        expect(indicator.hidden).toBe(true);
    });

    test('guidelines-toggle on attaches evidence_query=message to the send', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"reply":"ok"}'),
        });
        const toggle = panel.querySelector('[data-role="guidelines-toggle"]');
        toggle.checked = true;

        submitMessage(panel, 'A1C target adult diabetes');
        await flush();

        const opts = global.fetch.mock.calls[0][1];
        const body = JSON.parse(opts.body);
        expect(body.evidence_query).toBe('A1C target adult diabetes');
    });

    test('guidelines-toggle off omits evidence_query', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"reply":"ok"}'),
        });

        submitMessage(panel, 'how is this patient?');
        await flush();

        const opts = global.fetch.mock.calls[0][1];
        const body = JSON.parse(opts.body);
        expect('evidence_query' in body).toBe(false);
    });

    test('plain chart-question turn (no upload, toggle off) is unchanged', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"reply":"ok"}'),
        });

        submitMessage(panel, 'medication list?');
        await flush();

        const opts = global.fetch.mock.calls[0][1];
        const body = JSON.parse(opts.body);
        expect(body.message).toBe('medication list?');
        expect('document_id' in body).toBe(false);
        expect('evidence_query' in body).toBe(false);
    });
});


describe('agent_panel without W2 widgets (regression)', () => {
    test('panel without upload elements still binds the form and sends', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"reply":"chart-only"}'),
        });
        const panel = buildPanelWithoutUpload();

        submitMessage(panel, 'medication list?');
        await flush();

        expect(global.fetch).toHaveBeenCalledTimes(1);
        const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
        // user bubble + agent bubble
        expect(msgs.length).toBe(2);
    });
});
