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

    function send(panel, message) {
        var url = panel.getAttribute('data-turn-url') || '';
        var messagesEl = $(panel, 'messages');
        appendMessage(messagesEl, 'user', message);
        setBusy(panel, true);

        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain;q=0.9, */*;q=0.5'
            },
            body: JSON.stringify({ message: message })
        }).then(function (response) {
            return response.text().then(function (body) {
                if (!response.ok) {
                    appendMessage(messagesEl, 'error', extractError(response.status, body));
                    return;
                }
                appendMessage(messagesEl, 'agent', extractReply(body));
            });
        }).catch(function (err) {
            var msg = (err && err.message) ? err.message : 'Network error';
            appendMessage(messagesEl, 'error', 'Error: ' + msg);
        }).then(function () {
            setBusy(panel, false);
        });
    }

    function bind(panel) {
        if (panel.dataset.agentforgeBound === '1') {
            return;
        }
        panel.dataset.agentforgeBound = '1';

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
