/**
 * Tests for agent_panel.js SSE streaming (week1-gaps Task #12).
 *
 * Loads the IIFE into jsdom, mocks fetch, drives form submit, and
 * asserts DOM state after the stream resolves.
 *
 * @jest-environment jsdom
 */

'use strict';

const fs = require('fs');
const path = require('path');

// jsdom doesn't expose Node's TextDecoder/TextEncoder globals by default.
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

// Flush pending microtasks deeply enough to drain all chained .then()
// handlers from the fetch → read → read → … promise chain.
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

// Return a mock ReadableStream body whose reader yields one Buffer per frame.
function makeStreamBody(frames) {
    const chunks = frames.map(f => Buffer.from(f, 'utf8'));
    let idx = 0;
    return {
        getReader: () => ({
            read: () =>
                idx < chunks.length
                    ? Promise.resolve({ done: false, value: chunks[idx++] })
                    : Promise.resolve({ done: true, value: undefined }),
        }),
    };
}

function submitMessage(panel, text) {
    const input = panel.querySelector('[data-role="input"]');
    const form = panel.querySelector('[data-role="form"]');
    input.value = text;
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
}

function agentBubbleText(panel) {
    const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
    const last = msgs[msgs.length - 1];
    return last ? last.querySelector('div').textContent : '';
}

// ── SSE streaming path ────────────────────────────────────────────────────────

describe('agent_panel SSE streaming', () => {
    let panel;

    beforeEach(() => {
        jest.clearAllMocks();
        panel = buildPanel();
    });

    test('text deltas are appended to streaming bubble in order', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: (n) => n.toLowerCase() === 'content-type' ? 'text/event-stream' : null },
            body: makeStreamBody([
                'data: {"text":"Hello, "}\n\n',
                'data: {"text":"Susan!"}\n\n',
                'data: {"final":true,"stop_reason":"end_turn","cost_usd":0.001}\n\n',
                'data: [DONE]\n\n',
            ]),
        });

        submitMessage(panel, 'Who is this patient?');
        await flush();

        const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
        // First bubble = user; second = streaming agent bubble.
        expect(msgs.length).toBe(2);
        expect(msgs[1].querySelector('div').textContent).toBe('Hello, Susan!');
    });

    test('stream stops cleanly after [DONE] sentinel', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: (n) => n.toLowerCase() === 'content-type' ? 'text/event-stream' : null },
            body: makeStreamBody([
                'data: {"text":"ok"}\n\n',
                'data: [DONE]\n\n',
                // Anything after [DONE] must be ignored.
                'data: {"text":"should not appear"}\n\n',
            ]),
        });

        submitMessage(panel, 'Anything?');
        await flush();

        expect(agentBubbleText(panel)).toBe('ok');
    });

    test('SSE frame split across two read() calls is assembled correctly', async () => {
        // The frame `data: {"text":"partial"}` is split at the colon after "te".
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: (n) => n.toLowerCase() === 'content-type' ? 'text/event-stream' : null },
            body: makeStreamBody([
                'data: {"te',
                'xt":"partial"}\n\n',
                'data: [DONE]\n\n',
            ]),
        });

        submitMessage(panel, 'Test?');
        await flush();

        expect(agentBubbleText(panel)).toBe('partial');
    });

    test('empty-state element is removed when stream begins', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: (n) => n.toLowerCase() === 'content-type' ? 'text/event-stream' : null },
            body: makeStreamBody(['data: {"text":"hi"}\n\n', 'data: [DONE]\n\n']),
        });

        submitMessage(panel, 'Hello?');
        await flush();

        expect(panel.querySelector('[data-role="empty-state"]')).toBeNull();
    });

    test('fetch Accept header prefers text/event-stream', async () => {
        let capturedHeaders = {};
        global.fetch = jest.fn().mockImplementation((url, opts) => {
            capturedHeaders = opts.headers || {};
            return Promise.resolve({
                ok: true,
                headers: { get: () => null },
                text: () => Promise.resolve('{"reply":"ok"}'),
            });
        });

        submitMessage(panel, 'Hi');
        await flush();

        const accept = capturedHeaders['Accept'] || '';
        expect(accept).toContain('text/event-stream');
    });
});

// ── Non-streaming fallback path ───────────────────────────────────────────────

describe('agent_panel non-streaming fallback', () => {
    let panel;

    beforeEach(() => {
        jest.clearAllMocks();
        panel = buildPanel();
    });

    test('JSON response is rendered via extractReply', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => 'application/json' },
            text: () => Promise.resolve('{"reply":"Stable patient."}'),
        });

        submitMessage(panel, 'How is the patient?');
        await flush();

        expect(agentBubbleText(panel)).toBe('Stable patient.');
    });

    test('non-200 SSE response shows error bubble', async () => {
        global.fetch = jest.fn().mockResolvedValue({
            ok: false,
            status: 503,
            headers: { get: () => null },
            text: () => Promise.resolve('{"error":"sidecar unavailable"}'),
        });

        submitMessage(panel, 'Any issues?');
        await flush();

        const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
        expect(msgs.length).toBe(2);
        expect(msgs[1].querySelector('div').textContent).toContain('503');
    });

    test('network error shows error bubble', async () => {
        global.fetch = jest.fn().mockRejectedValue(new Error('Connection refused'));

        submitMessage(panel, 'Anything?');
        await flush();

        const msgs = panel.querySelectorAll('[data-role="messages"] .agentforge-message');
        expect(msgs.length).toBe(2);
        expect(msgs[1].querySelector('div').textContent).toContain('Connection refused');
    });
});
