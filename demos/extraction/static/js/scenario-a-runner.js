// scenario-a-runner.js — PRD-004 v5 Phase 5A deliverable I (part 1).
//
// Wires the Scenario A view's controls to /api/scenario-a/{run,analyze}.
// Frontend-side state:
//   scenarioAState.lastResponse — the most recent /run response, used by
//     the Analyze button.

(function () {
    'use strict';

    const TICKERS = [
        { value: 'AAPL', label: 'AAPL — Apple Inc.', goldAvailable: true },
        { value: 'AMD', label: 'AMD — Advanced Micro Devices', goldAvailable: false },
        { value: 'GOOGL', label: 'GOOGL — Alphabet Inc.', goldAvailable: false },
        { value: 'JNJ', label: 'JNJ — Johnson & Johnson', goldAvailable: true },
        { value: 'MSFT', label: 'MSFT — Microsoft Corp.', goldAvailable: false },
        { value: 'NVDA', label: 'NVDA — NVIDIA Corp.', goldAvailable: false },
        { value: 'UNH', label: 'UNH — UnitedHealth Group', goldAvailable: false },
        { value: 'JNJ-Stelara', label: 'JNJ-Stelara — Stelara trial (clinical)', goldAvailable: true },
    ];

    const scenarioAState = {
        lastResponse: null,
    };
    window.scenarioAState = scenarioAState;

    function populateTickerPickers() {
        const picker = document.getElementById('scenario-a-ticker-picker');
        if (!picker) return;
        picker.innerHTML = '<option value="">— Select ticker —</option>';
        for (const t of TICKERS) {
            const opt = document.createElement('option');
            opt.value = t.value;
            opt.textContent = t.label + (t.goldAvailable ? '   ⭐ gold available' : '   (qualitative only)');
            opt.dataset.gold = t.goldAvailable ? '1' : '0';
            picker.appendChild(opt);
        }
    }

    function getModeFromRadios() {
        const radios = document.querySelectorAll('input[name="scenario-a-mode"]');
        for (const r of radios) {
            if (r.checked) return r.value;
        }
        return 'A2';
    }

    function setStatus(msg) {
        const el = document.getElementById('scenario-a-status');
        if (el) el.textContent = msg || '';
    }

    function setAnswer(target, text) {
        const el = document.getElementById(target);
        if (el) el.textContent = text || '';
    }

    function setMode(mode) {
        const badge = document.getElementById('scenario-a-mode-badge');
        if (badge) {
            const labels = { A1: 'standard', A2: '+1-hop', A3: 'graph-as-corpus' };
            badge.textContent = labels[mode] || mode;
        }
    }

    async function runScenarioA() {
        const question = (document.getElementById('scenario-a-question-input') || {}).value || '';
        const ticker = (document.getElementById('scenario-a-ticker-picker') || {}).value || '';
        const mode = getModeFromRadios();
        if (!question.trim() || !ticker) {
            setStatus('Question and ticker are required.');
            return;
        }
        const runBtn = document.getElementById('scenario-a-run-btn');
        const analyzeBtn = document.getElementById('scenario-a-analyze-btn');
        if (runBtn) runBtn.disabled = true;
        if (analyzeBtn) analyzeBtn.hidden = true;
        setMode(mode);
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
                if (data.detail) setAnswer('scenario-a-dense-answer', data.detail);
                return;
            }
            scenarioAState.lastResponse = { question, ticker, mode, ...data };
            setAnswer('scenario-a-dense-answer', data.dense_answer);
            setAnswer('scenario-a-graphrag-answer', data.graphrag_answer);
            setAnswer('scenario-a-dense-context', data.retrieved_context_left);
            setAnswer('scenario-a-graphrag-context', data.retrieved_context_right);
            if (analyzeBtn) analyzeBtn.hidden = false;
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
            const verdict = document.getElementById('scenario-a-verdict');
            const winnerEl = document.getElementById('scenario-a-verdict-winner');
            const rationaleA = document.getElementById('scenario-a-verdict-rationale-a');
            const rationaleB = document.getElementById('scenario-a-verdict-rationale-b');
            const summary = document.getElementById('scenario-a-verdict-summary');
            if (winnerEl) {
                const winnerText = data.winner === 'A' ? 'Winner: Dense RAG'
                    : data.winner === 'B' ? 'Winner: GraphRAG'
                    : 'Verdict: tie';
                winnerEl.textContent = winnerText;
            }
            if (rationaleA) rationaleA.textContent = data.rationale_a || '';
            if (rationaleB) rationaleB.textContent = data.rationale_b || '';
            if (summary) summary.textContent = data.verdict || '';
            if (verdict) verdict.hidden = false;
            setStatus('Done.');
        } catch (e) {
            setStatus(`Judge failed: ${e.message}`);
        }
    }

    // Action wiring (Wave E delegation; same pattern as the rest of compare-runner).
    if (typeof registerAction === 'function') {
        registerAction('scenario-a-run', () => runScenarioA());
        registerAction('scenario-a-analyze', () => analyzeScenarioA());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', populateTickerPickers);
    } else {
        populateTickerPickers();
    }
})();
