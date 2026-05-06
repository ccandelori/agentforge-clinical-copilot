/**
 * AgentForge Clinical Co-Pilot — chat panel wiring.
 *
 * Plain IIFE; no ES modules. Loaded via a <script> tag from the OpenEMR page
 * that renders agent_panel.html.twig. Auto-binds to any `.agentforge-panel`
 * element present on DOMContentLoaded. The panel reads its turn endpoint URL
 * from `data-turn-url`. Patient context is server-side (sidecar reads `pid`
 * from the OpenEMR session), so the JS only POSTs the message text.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Cameron Candelori <cameron.candelori@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Cameron Candelori
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */
(function () {
    'use strict';

    function $(panel, role) {
        return panel.querySelector('[data-role="' + role + '"]');
    }

    function appendMessage(messagesEl, role, text) {
        var emptyState = messagesEl.querySelector('[data-role="empty-state"]');
        if (emptyState) {
            emptyState.remove();
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'agentforge-message mb-2 d-flex';
        if (role === 'user') {
            wrapper.classList.add('justify-content-end');
        }
        var bubble = document.createElement('div');
        bubble.className = 'px-2 py-1 rounded';
        bubble.style.maxWidth = '85%';
        bubble.style.whiteSpace = 'pre-wrap';
        bubble.style.wordBreak = 'break-word';
        if (role === 'user') {
            bubble.classList.add('bg-primary', 'text-white');
        } else if (role === 'agent') {
            bubble.classList.add('bg-white', 'border');
        } else {
            // error
            bubble.classList.add('bg-danger', 'text-white');
        }
        bubble.textContent = text;
        wrapper.appendChild(bubble);
        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    /**
     * Render the structured extraction beneath the agent's chat
     * bubble (W2 INTAKE flow, MR 7 follow-up).
     *
     * The synthesized chat reply is a clinician-facing summary; this
     * panel is the receipts — every field the vision extractor
     * produced, pretty-printed, so a clinician can confirm the
     * underlying data before acting on the summary. Default-collapsed
     * via the native ``<details>`` element so the chat scroll stays
     * compact; the user clicks to expand.
     *
     * No-ops on null / undefined / empty extraction so the JS-side
     * call is safe for chart-question turns and evidence-only turns
     * where the backend returns ``extraction: null``.
     */
    function appendExtractionPanel(messagesEl, extraction) {
        if (extraction === null || extraction === undefined) {
            return;
        }
        if (typeof extraction !== 'object') {
            return;
        }
        var details = document.createElement('details');
        details.className = 'agentforge-extraction-panel mb-2 small';
        details.setAttribute('data-role', 'extraction-panel');
        // Default-collapsed; the user opts into the JSON dump.
        details.open = false;

        var summary = document.createElement('summary');
        summary.className = 'text-muted';
        summary.style.cursor = 'pointer';
        summary.textContent = 'Extracted fields (click to expand)';
        details.appendChild(summary);

        var pre = document.createElement('pre');
        pre.className = 'bg-light border rounded p-2 mt-1 mb-0';
        pre.style.maxHeight = '320px';
        pre.style.overflowY = 'auto';
        pre.style.whiteSpace = 'pre-wrap';
        pre.style.wordBreak = 'break-word';
        try {
            pre.textContent = JSON.stringify(extraction, null, 2);
        } catch (e) {
            pre.textContent = String(extraction);
        }
        details.appendChild(pre);

        messagesEl.appendChild(details);
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function setBusy(panel, busy) {
        var input = $(panel, 'input');
        var send = $(panel, 'send');
        var status = $(panel, 'status');
        input.disabled = busy;
        send.disabled = busy;
        if (busy) {
            status.hidden = false;
        } else {
            status.hidden = true;
            input.focus();
        }
    }

    function extractReply(rawBody) {
        var text = (rawBody || '').trim();
        if (text === '') {
            return '(empty response)';
        }
        try {
            var parsed = JSON.parse(text);
            if (parsed && typeof parsed === 'object') {
                if (typeof parsed.reply === 'string') {
                    return parsed.reply;
                }
                if (typeof parsed.message === 'string') {
                    return parsed.message;
                }
            }
        } catch (e) {
            // not JSON — fall through to raw text
        }
        return text;
    }

    /**
     * Pull the structured extraction off the parsed JSON body, if
     * present. Returns null on missing-field / non-object / parse
     * errors so the caller can guard with a null check rather than
     * a try/catch.
     */
    function extractExtraction(rawBody) {
        var text = (rawBody || '').trim();
        if (text === '') {
            return null;
        }
        try {
            var parsed = JSON.parse(text);
            if (parsed && typeof parsed === 'object' && parsed.extraction) {
                if (typeof parsed.extraction === 'object') {
                    return parsed.extraction;
                }
            }
        } catch (e) {
            // not JSON — fall through; no extraction to render
        }
        return null;
    }

    function extractError(status, rawBody) {
        var detail = '';
        var text = (rawBody || '').trim();
        if (text !== '') {
            try {
                var parsed = JSON.parse(text);
                if (parsed && typeof parsed === 'object') {
                    detail = parsed.error || parsed.message || parsed.detail || '';
                }
            } catch (e) {
                detail = text.length > 200 ? text.substring(0, 200) + '…' : text;
            }
        }
        var msg = 'Error ' + status;
        if (detail) {
            msg += ': ' + detail;
        }
        return msg;
    }

    /**
     * Mint a session id for a fresh conversation. Server-side conversation
     * memory is keyed on this value (sidecar TurnRequest.session_id, see
     * orchestrator/memory.py). Without it every turn is independent and
     * the agent has no recollection of previous messages in the chat.
     *
     * crypto.randomUUID is available on every modern browser (Safari 15.4+,
     * Chrome 92+, Firefox 95+) and only requires a secure context, which
     * the OpenEMR module always runs in. Fall back to timestamp+random if
     * the page is somehow served over insecure http.
     */
    function generateSessionId() {
        if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
            return crypto.randomUUID();
        }
        return 'sid-' + Date.now().toString(36) + '-' + Math.random().toString(36).substring(2, 10);
    }

    /**
     * Create an empty agent bubble for incremental token fill.
     * Mirrors appendMessage('agent', ...) but leaves text empty.
     */
    function createStreamingBubble(messagesEl) {
        var emptyState = messagesEl.querySelector('[data-role="empty-state"]');
        if (emptyState) {
            emptyState.remove();
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'agentforge-message mb-2 d-flex';
        var bubble = document.createElement('div');
        bubble.className = 'px-2 py-1 rounded bg-white border';
        bubble.style.maxWidth = '85%';
        bubble.style.whiteSpace = 'pre-wrap';
        bubble.style.wordBreak = 'break-word';
        wrapper.appendChild(bubble);
        messagesEl.appendChild(wrapper);
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return bubble;
    }

    /**
     * Read SSE frames from a ReadableStream reader and append text
     * tokens to ``bubble`` as they arrive.
     *
     * Wire shape the sidecar emits (see main.py _sse_stream):
     *   data: {"text": "..."}\n\n      — text delta
     *   data: {"final": true, ...}\n\n — cost / stop_reason (informational)
     *   data: [DONE]\n\n               — clean stream end
     *
     * The ``remaining`` buffer accumulates bytes between read() calls so
     * a frame split across two chunks is reassembled before JSON.parse.
     */
    function consumeSseStream(reader, bubble, messagesEl) {
        var decoder = new TextDecoder();
        var remaining = '';

        function readNext() {
            return reader.read().then(function (result) {
                if (result.done) {
                    return;
                }
                remaining += decoder.decode(result.value, { stream: true });
                var lines = remaining.split('\n');
                // Keep the last (potentially incomplete) line for the next chunk.
                remaining = lines[lines.length - 1];
                for (var i = 0; i < lines.length - 1; i++) {
                    var line = lines[i].trimEnd();
                    if (line.indexOf('data: ') !== 0) {
                        continue;
                    }
                    var data = line.slice(6);
                    if (data === '[DONE]') {
                        return; // clean end — stop reading
                    }
                    try {
                        var parsed = JSON.parse(data);
                        if (parsed && typeof parsed.text === 'string') {
                            bubble.textContent += parsed.text;
                            messagesEl.scrollTop = messagesEl.scrollHeight;
                        }
                        // final frame carries cost_usd + stop_reason; no
                        // visible action needed here — JS already has it.
                    } catch (e) {
                        // malformed frame — skip silently
                    }
                }
                return readNext();
            });
        }

        return readNext();
    }

    /**
     * Mark a document as attached to the next /turn send. Stored on the
     * panel dataset so state survives any DOM re-render and so the
     * test harness can assert against it without poking JS internals.
     */
    function setPendingDocumentId(panel, docId, filename) {
        panel.dataset.pendingDocumentId = docId;
        var indicator = $(panel, 'pending-doc-indicator');
        if (indicator) {
            indicator.hidden = false;
            indicator.textContent =
                'Attached: ' + (filename || 'document') + ' (#' + docId + ')';
        }
    }

    function clearPendingDocumentId(panel) {
        delete panel.dataset.pendingDocumentId;
        var indicator = $(panel, 'pending-doc-indicator');
        if (indicator) {
            indicator.hidden = true;
            indicator.textContent = '';
        }
    }

    /**
     * Wire the W2 upload widget if the panel renders one (MR 7).
     *
     * Optional — panels without ``[data-role="upload-button"]`` / a
     * ``data-upload-url`` attribute (chart-question-only deployments)
     * skip the wiring entirely. The button click fans out to a hidden
     * file input so the page styles can place the button anywhere
     * without inheriting the file-picker chrome.
     */
    function bindUploadWidget(panel) {
        var uploadUrl = panel.getAttribute('data-upload-url') || '';
        var uploadButton = $(panel, 'upload-button');
        var uploadInput = $(panel, 'upload-input');
        if (!uploadUrl || !uploadButton || !uploadInput) {
            return;
        }

        uploadButton.addEventListener('click', function (event) {
            event.preventDefault();
            uploadInput.click();
        });

        uploadInput.addEventListener('change', function () {
            var file = uploadInput.files && uploadInput.files[0];
            if (!file) {
                return;
            }
            var formData = new FormData();
            formData.append('file', file);
            formData.append('doc_type', 'intake_form');
            // OpenEMR's globals-loaded endpoints reject any POST that
            // doesn't carry a session-bound CSRF token. The OpenEMR top
            // frame exposes the token as ``top.csrf_token_js`` (set in
            // interface/main/tabs/main.php); upload_document.php reads
            // it from the ``csrf_token_form`` multipart field. Same
            // shape as library/js/dwv/dicom_gui.js.
            var csrfToken = '';
            try {
                if (typeof top !== 'undefined' && top && typeof top.csrf_token_js === 'string') {
                    csrfToken = top.csrf_token_js;
                }
            } catch (e) {
                // Cross-origin top access throws — fall through to the
                // empty-token path; upload_document.php will return 403
                // with a clear error bubble for the user.
            }
            formData.append('csrf_token_form', csrfToken);

            fetch(uploadUrl, {
                method: 'POST',
                credentials: 'same-origin',
                body: formData
            }).then(function (response) {
                return response.text().then(function (rawBody) {
                    var messagesEl = $(panel, 'messages');
                    if (!response.ok) {
                        appendMessage(
                            messagesEl,
                            'error',
                            extractError(response.status, rawBody)
                        );
                        return;
                    }
                    var docId = '';
                    try {
                        var parsed = JSON.parse(rawBody);
                        if (parsed && typeof parsed.document_id === 'number') {
                            docId = String(parsed.document_id);
                        }
                    } catch (e) {
                        // not JSON — fall through; docId stays empty
                    }
                    if (docId !== '') {
                        setPendingDocumentId(panel, docId, file.name);
                    } else {
                        appendMessage(
                            messagesEl,
                            'error',
                            'Upload succeeded but no document_id was returned.'
                        );
                    }
                });
            }).catch(function (err) {
                var msg = (err && err.message) ? err.message : 'Network error';
                appendMessage(
                    $(panel, 'messages'),
                    'error',
                    'Upload error: ' + msg
                );
            });
            // Reset the input so picking the same file twice fires a
            // fresh ``change`` event.
            uploadInput.value = '';
        });
    }

    function send(panel, message) {
        var url = panel.getAttribute('data-turn-url') || '';
        var messagesEl = $(panel, 'messages');
        appendMessage(messagesEl, 'user', message);
        setBusy(panel, true);

        var sessionId = panel.dataset.sessionId || '';
        var body = { message: message };
        if (sessionId !== '') {
            body.session_id = sessionId;
        }

        // W2 inputs (MR 7). ``document_id`` rides one turn — clear the
        // pending state immediately so a follow-up chat message
        // doesn't re-attach the same upload. ``evidence_query`` is
        // sticky-on while the toggle stays checked: clinicians often
        // ask several guideline questions in a row.
        var pendingId = panel.dataset.pendingDocumentId || '';
        if (pendingId !== '') {
            var parsed = parseInt(pendingId, 10);
            if (!isNaN(parsed)) {
                body.document_id = parsed;
            }
            clearPendingDocumentId(panel);
        }
        var guidelinesToggle = $(panel, 'guidelines-toggle');
        if (guidelinesToggle && guidelinesToggle.checked) {
            body.evidence_query = message;
        }

        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                // Prefer SSE so the sidecar streams tokens as they are
                // produced; fall back to JSON for older/buffered paths.
                'Accept': 'text/event-stream, application/json;q=0.9'
            },
            body: JSON.stringify(body)
        }).then(function (response) {
            if (!response.ok) {
                return response.text().then(function (rawBody) {
                    appendMessage(messagesEl, 'error', extractError(response.status, rawBody));
                });
            }
            var ct = (response.headers.get('content-type') || '').toLowerCase();
            if (ct.indexOf('text/event-stream') !== -1 && response.body) {
                // SSE streaming path — tokens appear incrementally.
                var bubble = createStreamingBubble(messagesEl);
                return consumeSseStream(response.body.getReader(), bubble, messagesEl);
            }
            // Non-streaming fallback — render full reply at once.
            // W2 INTAKE turns also append the structured-extraction
            // panel beneath the bubble so the clinician can confirm
            // what was actually parsed from the PDF.
            return response.text().then(function (rawBody) {
                appendMessage(messagesEl, 'agent', extractReply(rawBody));
                appendExtractionPanel(messagesEl, extractExtraction(rawBody));
            });
        }).catch(function (err) {
            var msg = (err && err.message) ? err.message : 'Network error';
            appendMessage(messagesEl, 'error', 'Error: ' + msg);
        }).then(function () {
            setBusy(panel, false);
        });
    }

    function resetConversation(panel) {
        var messagesEl = $(panel, 'messages');
        // Mint a fresh session id; sidecar treats this as a brand-new
        // conversation with no memory of prior turns.
        panel.dataset.sessionId = generateSessionId();
        // Wipe rendered history but keep the empty-state hint so the
        // surface looks identical to first mount.
        while (messagesEl.firstChild) {
            messagesEl.removeChild(messagesEl.firstChild);
        }
        var emptyState = document.createElement('div');
        emptyState.className = 'text-muted small';
        emptyState.setAttribute('data-role', 'empty-state');
        emptyState.textContent = 'Ask the Co-Pilot a question about this patient.';
        messagesEl.appendChild(emptyState);
    }

    function bind(panel) {
        if (panel.dataset.agentforgeBound === '1') {
            return;
        }
        panel.dataset.agentforgeBound = '1';

        // Mint once on mount; reused across every turn until the user
        // clicks "New conversation". Persisted on the panel dataset so
        // it survives any DOM re-rendering of the messages list.
        panel.dataset.sessionId = generateSessionId();

        var form = $(panel, 'form');
        var input = $(panel, 'input');
        if (!form || !input) {
            return;
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var text = (input.value || '').trim();
            if (text === '') {
                return;
            }
            input.value = '';
            send(panel, text);
        });

        var resetBtn = $(panel, 'new-conversation');
        if (resetBtn) {
            resetBtn.addEventListener('click', function (event) {
                event.preventDefault();
                resetConversation(panel);
            });
        }

        // Optional W2 upload widget (MR 7). Bind only when the
        // template renders the upload row; chart-question-only
        // panels skip the wiring transparently.
        bindUploadWidget(panel);
    }

    function init() {
        var panels = document.querySelectorAll('.agentforge-panel');
        for (var i = 0; i < panels.length; i++) {
            bind(panels[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
