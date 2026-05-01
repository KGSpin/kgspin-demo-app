// scenario-a-runner.js — PRD-004 v5 Phase 5A fixup-20260430 commit 2.
//
// Modal-context-aware Single-shot Q&A runner. Lives inside the
// per-graph modal Why tab (Single-shot sub-tab). Reads ticker +
// pipeline from the active slot binding (no ticker picker — slot
// owns it). Single-shot sub-tab merges a templated-scenario picker
// (prefills the textarea) with a free-text textarea ("type your
// own"). Both paths feed `/api/scenario-a/run`.
//
// Slot context handshake:
//   - `expandedSlot` (int) and `slotState[expandedSlot]` (object)
//     are the slot binding source of truth — declared in slots.js
//     and reachable via cross-script script-scope.
//   - `currentDomain` (string) is set by domain-switch.js. Per
//     fixup F4/B1, we read it from `document.body.dataset.currentDomain`
//     instead of `window.currentDomain` (top-level `let` doesn't
//     bind to `window`).
//   - Ticker is read from `doc-id-input` (financial) or
//     `trial-select` (clinical) — same pattern as the existing
//     `triggerModalWhyThisMatters` handler.

(function () {
    'use strict';

    const scenarioAState = {
        // Last successful run, used by Analyze.
        lastResponse: null,
        // Modal-domain lock — set by initModalScenarioA on modal Why-tab open
        // (per fixup F14). Picker filtering uses this; mid-session domain
        // flips don't restage the picker.
        lockedDomain: null,
        templatesByScenarioId: {},
    };
    window.scenarioAState = scenarioAState;

    // ----- Slot context -----------------------------------------------------

    function getSlotDomain() {
        // Prefer slot's pipeline meta over the global currentDomain.
        if (typeof expandedSlot !== 'undefined' && expandedSlot !== null
            && typeof slotState !== 'undefined' && slotState[expandedSlot]) {
            const slot = slotState[expandedSlot];
            const meta = (typeof PIPELINE_META !== 'undefined') ? PIPELINE_META[slot.pipeline] : null;
            if (meta && meta.domain) return meta.domain;
        }
        // Fallback to body-dataset (cross-script handshake; per fixup F4/B1).
        return document.body.dataset.currentDomain
            || (typeof currentDomain !== 'undefined' ? currentDomain : '')
            || null;
    }

    function getSlotTicker() {
        const domain = getSlotDomain();
        if (domain === 'clinical') {
            const sel = document.getElementById('trial-select');
            return (sel && sel.value) ? sel.value.trim() : '';
        }
        const input = document.getElementById('doc-id-input');
        return (input && input.value) ? input.value.trim().toUpperCase() : '';
    }

    function setStatus(msg) {
        const el = document.getElementById('modal-scenario-a-status');
        if (el) el.textContent = msg || '';
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text || '';
    }

    function getModeFromRadios() {
        const radios = document.querySelectorAll('input[name="modal-scenario-a-mode"]');
        for (const r of radios) {
            if (r.checked) return r.value;
        }
        return 'A2';
    }

    function setModeBadge(mode) {
        const badge = document.getElementById('modal-scenario-a-mode-badge');
        if (badge) {
            const labels = { A1: 'standard', A2: '+1-hop', A3: 'graph-as-corpus' };
            badge.textContent = labels[mode] || mode;
        }
    }

    // ----- Run / Analyze ----------------------------------------------------

    async function runScenarioA() {
        const ticker = getSlotTicker();
        if (!ticker) {
            setStatus('No slot context — close and re-expand a slot.');
            return;
        }
        const textarea = document.getElementById('modal-scenario-a-question-input');
        const question = (textarea && textarea.value || '').trim();
        if (!question) {
            setStatus('Type a question or pick a scenario first.');
            if (textarea) textarea.focus();
            return;
        }
        const mode = getModeFromRadios();
        const runBtn = document.getElementById('modal-scenario-a-run-btn');
        const analyzeBtn = document.getElementById('modal-scenario-a-analyze-btn');
        const placeholder = document.getElementById('modal-scenario-a-placeholder');
        const verdict = document.getElementById('modal-scenario-a-verdict');
        if (runBtn) runBtn.disabled = true;
        if (analyzeBtn) analyzeBtn.disabled = true;
        if (verdict) verdict.hidden = true;
        if (placeholder) placeholder.hidden = false;
        setModeBadge(mode);
        setStatus('Running both panes…');

        try {
            const res = await fetch('/api/scenario-a/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question, ticker, mode }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                setStatus(`Error: ${data.error || res.status}`);
                if (data.detail) setText('modal-scenario-a-dense-answer', data.detail);
                return;
            }
            scenarioAState.lastResponse = { question, ticker, mode, ...data };
            setText('modal-scenario-a-dense-answer', data.dense_answer);
            setText('modal-scenario-a-graphrag-answer', data.graphrag_answer);
            setText('modal-scenario-a-dense-context', data.retrieved_context_left);
            setText('modal-scenario-a-graphrag-context', data.retrieved_context_right);
            if (analyzeBtn) analyzeBtn.disabled = false;
            setStatus('Done.');
        } catch (e) {
            setStatus(`Run failed: ${e.message}`);
        } finally {
            if (runBtn) runBtn.disabled = false;
        }
    }

    async function analyzeScenarioA() {
        const last = scenarioAState.lastResponse;
        if (!last) {
            setStatus('Run a question first.');
            return;
        }
        setStatus('Asking the blinded judge…');
        try {
            const res = await fetch('/api/scenario-a/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question: last.question,
                    dense_answer: last.dense_answer,
                    graphrag_answer: last.graphrag_answer,
                }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                setStatus(`Judge error: ${data.error || res.status}`);
                return;
            }
            const placeholder = document.getElementById('modal-scenario-a-placeholder');
            const verdict = document.getElementById('modal-scenario-a-verdict');
            const winnerEl = document.getElementById('modal-scenario-a-verdict-winner');
            const rationaleA = document.getElementById('modal-scenario-a-verdict-rationale-a');
            const rationaleB = document.getElementById('modal-scenario-a-verdict-rationale-b');
            const summary = document.getElementById('modal-scenario-a-verdict-summary');
            if (winnerEl) {
                const winnerText = data.winner === 'A' ? 'Winner: Dense RAG'
                    : data.winner === 'B' ? 'Winner: GraphRAG'
                    : 'Verdict: tie';
                winnerEl.textContent = winnerText;
            }
            if (rationaleA) rationaleA.textContent = data.rationale_a || '';
            if (rationaleB) rationaleB.textContent = data.rationale_b || '';
            if (summary) summary.textContent = data.verdict || '';
            if (placeholder) placeholder.hidden = true;
            if (verdict) verdict.hidden = false;
            setStatus('Done.');
        } catch (e) {
            setStatus(`Judge failed: ${e.message}`);
        }
    }

    // ----- Template-picker prefill ------------------------------------------

    function onTemplatePicked(el) {
        const sid = el.value || '';
        if (!sid) return;
        const tpl = scenarioAState.templatesByScenarioId[sid];
        if (!tpl) return;
        // Prefill the textarea with the template's resolved-question
        // preview. The user can edit before Run; if they Run as-is,
        // the same /api/scenario-a/run path handles it.
        const preview = renderTemplatePreview(tpl);
        const textarea = document.getElementById('modal-scenario-a-question-input');
        if (textarea) textarea.value = preview;
    }

    // Best-effort placeholder substitution for the preview UI; the
    // backend resolves authoritatively at /api/scenario-a/run via
    // free-text. Same map used by the multi-hop runner — kept inline
    // here for runner self-containment.
    function renderTemplatePreview(tpl) {
        const ticker = getSlotTicker() || '{ticker}';
        const tickerToCompany = {
            AAPL: 'Apple Inc.', AMD: 'Advanced Micro Devices, Inc.',
            GOOGL: 'Alphabet Inc.', JNJ: 'Johnson & Johnson',
            MSFT: 'Microsoft Corporation', NVDA: 'NVIDIA Corporation',
            UNH: 'UnitedHealth Group', 'JNJ-Stelara': 'Johnson & Johnson',
        };
        let preview = (tpl.question_template || '').toString();
        preview = preview.replace(/\{company\}/g, tickerToCompany[ticker] || ticker);
        preview = preview.replace(/\{ticker\}/g, ticker);
        preview = preview.replace(/\{year\}/g, '2025');
        preview = preview.replace(/\{drug\}/g, 'Stelara');
        preview = preview.replace(/\{sponsor\}/g, 'Centocor, Inc.');
        preview = preview.replace(/\{trial_id\}/g, 'NCT00174785');
        return preview.replace(/\s+/g, ' ').trim();
    }

    // ----- Modal Why-tab init (called by slots.js switchModalTab) -----------

    window.initModalScenarioA = function initModalScenarioA() {
        const ticker = getSlotTicker();
        const domain = getSlotDomain();
        scenarioAState.lockedDomain = domain;
        scenarioAState.lastResponse = null;

        // Header populated by slots.js initModalWhyTab; we just make
        // sure mode badge default is correct here.
        setModeBadge(getModeFromRadios());

        // Reset answer panes + verdict.
        setText('modal-scenario-a-dense-answer', '');
        setText('modal-scenario-a-graphrag-answer', '');
        setText('modal-scenario-a-dense-context', '');
        setText('modal-scenario-a-graphrag-context', '');
        const placeholder = document.getElementById('modal-scenario-a-placeholder');
        const verdict = document.getElementById('modal-scenario-a-verdict');
        if (placeholder) placeholder.hidden = false;
        if (verdict) verdict.hidden = true;
        const analyzeBtn = document.getElementById('modal-scenario-a-analyze-btn');
        if (analyzeBtn) analyzeBtn.disabled = true;

        // Defensive default per VP-Prod S5 / fixup F14: explicit error
        // state when slot context is missing (NOT a silent financial
        // fallback).
        if (!ticker || !domain) {
            setStatus('No slot context — close and re-expand a slot from the page above.');
            const runBtn = document.getElementById('modal-scenario-a-run-btn');
            if (runBtn) runBtn.disabled = true;
            return;
        }
        setStatus('');

        // Template picker is populated by scenario-b-runner.js's
        // template fetcher (commit 3) — same data, both sub-tabs.
    };

    // Picker change handler.
    if (typeof registerChangeAction === 'function') {
        registerChangeAction('modal-scenario-a-template-picker', (el) => onTemplatePicked(el));
    } else {
        document.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'modal-scenario-a-template-picker') {
                onTemplatePicked(e.target);
            }
        });
    }

    if (typeof registerAction === 'function') {
        registerAction('modal-scenario-a-run', () => runScenarioA());
        registerAction('modal-scenario-a-analyze', () => analyzeScenarioA());
    }
})();
