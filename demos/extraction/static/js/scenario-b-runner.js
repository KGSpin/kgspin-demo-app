// scenario-b-runner.js — PRD-004 v5 Phase 5A deliverable I (part 2).
//
// Wires the Scenario B view to /api/scenario-b/* endpoints. The /run
// endpoint streams SSE; we consume via fetch+ReadableStream so we
// can POST a JSON body (EventSource is GET-only).
//
// State machine:
//   Run → POST /api/scenario-b/run → consume `stage` events, terminal
//         `all_done` event carries pane_outputs.
//   Analyze → POST /api/scenario-b/analyze with the pane_outputs from
//         the last run; render F1 + rationales + (optional)
//         narrative_recovery.
//   Show advanced → toggle [data-pane-mode] on .scenario-b-panes;
//         reveal the tool-agent pane (Phase 5B preview).
//   URL hash deep-link: /compare#scenario-b?template=...&ticker=...&autorun=1
//         pre-selects the picker(s) and (optionally) clicks Run.

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
        { value: 'JNJ-Stelara', label: 'JNJ-Stelara — Stelara trial', goldAvailable: true },
    ];

    const GOLD_TICKER_BY_SCENARIO = {
        subsidiaries_litigation_jurisdiction: ['AAPL', 'JNJ'],
        neo_compensation_stock_awards: ['AAPL', 'JNJ'],
        segments_revenue_litigation_accrual: ['AAPL', 'JNJ'],
        supplier_concentration_ma_termination: ['AAPL', 'JNJ'],
        warrants_options_proxy_executives: ['AAPL', 'JNJ'],
        stelara_adverse_events_cohort_v5: ['JNJ-Stelara'],
    };

    const scenarioBState = {
        templates: [],
        templateById: {},
        selectedScenarioId: '',
        selectedTicker: '',
        lastResolvedQuestion: '',
        lastPaneOutputs: null,
        showAdvanced: false,
    };
    window.scenarioBState = scenarioBState;

    function setStatus(msg) {
        const el = document.getElementById('scenario-b-status');
        if (el) el.textContent = msg || '';
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text || '';
    }

    function populateTickerPicker() {
        const picker = document.getElementById('scenario-b-ticker-picker');
        if (!picker) return;
        picker.innerHTML = '<option value="">— Select ticker —</option>';
        for (const t of TICKERS) {
            const opt = document.createElement('option');
            opt.value = t.value;
            opt.textContent = t.label;
            picker.appendChild(opt);
        }
    }

    async function fetchTemplates() {
        try {
            const res = await fetch('/api/scenario-b/templates');
            if (!res.ok) return;
            const templates = await res.json();
            scenarioBState.templates = templates;
            scenarioBState.templateById = Object.fromEntries(templates.map(t => [t.scenario_id, t]));
            const picker = document.getElementById('scenario-b-template-picker');
            if (!picker) return;
            picker.innerHTML = '<option value="">— Select scenario —</option>';
            for (const t of templates) {
                const opt = document.createElement('option');
                opt.value = t.scenario_id;
                const hopBadge = t.expected_hops ? ` · ${t.expected_hops}-hop` : '';
                opt.textContent = `[${t.domain}/${t.expected_difficulty}${hopBadge}] ${t.scenario_id}`;
                picker.appendChild(opt);
            }
        } catch (e) {
            console.warn('[scenario-b] template fetch failed:', e);
        }
    }

    function updateGoldBadge() {
        const goldBadge = document.getElementById('scenario-b-gold-badge');
        const noGoldBadge = document.getElementById('scenario-b-no-gold-badge');
        if (!goldBadge || !noGoldBadge) return;
        const sid = scenarioBState.selectedScenarioId;
        const ticker = scenarioBState.selectedTicker;
        if (!sid || !ticker) {
            goldBadge.hidden = true;
            noGoldBadge.hidden = true;
            return;
        }
        const goldTickers = GOLD_TICKER_BY_SCENARIO[sid] || [];
        const hasGold = goldTickers.includes(ticker);
        goldBadge.hidden = !hasGold;
        noGoldBadge.hidden = hasGold;
    }

    function refreshResolvedQuestion() {
        const sid = scenarioBState.selectedScenarioId;
        const ticker = scenarioBState.selectedTicker;
        const wrap = document.getElementById('scenario-b-resolved-question');
        const text = document.getElementById('scenario-b-question-text');
        if (!sid || !ticker || !text || !wrap) {
            if (wrap) wrap.hidden = true;
            return;
        }
        // Render the template's raw question_template as a preview;
        // backend resolves it during /run. Best-effort placeholder
        // substitution for the preview.
        const tpl = scenarioBState.templateById[sid];
        if (!tpl) { wrap.hidden = true; return; }
        let preview = tpl.question_template;
        const tickerToCompany = {
            AAPL: 'Apple Inc.', AMD: 'Advanced Micro Devices, Inc.',
            GOOGL: 'Alphabet Inc.', JNJ: 'Johnson & Johnson',
            MSFT: 'Microsoft Corporation', NVDA: 'NVIDIA Corporation',
            UNH: 'UnitedHealth Group', 'JNJ-Stelara': 'Johnson & Johnson',
        };
        preview = preview.replace(/\{company\}/g, tickerToCompany[ticker] || ticker);
        preview = preview.replace(/\{ticker\}/g, ticker);
        preview = preview.replace(/\{year\}/g, '2025');
        preview = preview.replace(/\{drug\}/g, 'Stelara');
        preview = preview.replace(/\{sponsor\}/g, 'Centocor, Inc.');
        preview = preview.replace(/\{trial_id\}/g, 'NCT00174785');
        preview = preview.replace(/\s+/g, ' ').trim();
        text.textContent = preview;
        wrap.hidden = false;
        updateGoldBadge();
    }

    function appendStagePill(paneName, stageLabel, status) {
        const map = { agentic_dense: 'scenario-b-agentic-progress', paper_mirror: 'scenario-b-paper-progress' };
        const containerId = map[paneName];
        if (!containerId) return;
        const container = document.getElementById(containerId);
        if (!container) return;
        const pill = document.createElement('span');
        pill.className = `stage-pill ${status || 'active'}`;
        pill.textContent = stageLabel;
        container.appendChild(pill);
    }

    function clearProgress() {
        for (const id of ['scenario-b-agentic-progress', 'scenario-b-paper-progress']) {
            const c = document.getElementById(id);
            if (c) c.innerHTML = '';
        }
    }

    function renderPaneOutputs(paneOutputs) {
        if (paneOutputs.agentic_dense) {
            const a = paneOutputs.agentic_dense;
            setText('scenario-b-agentic-answer', a.final_answer);
            const trace = (a.decomposition_trace || []).map((q, i) => `${i + 1}. ${q}`).join('\n');
            setText('scenario-b-agentic-trace', trace);
        }
        if (paneOutputs.paper_mirror) {
            const p = paneOutputs.paper_mirror;
            setText('scenario-b-paper-answer', p.final_answer);
            const text_history = (p.retrieval_history?.text_channel || [])
                .map((s, i) => `Sub-query ${i + 1}: ${s.sub_query}\nAnswer: ${s.answer}`)
                .join('\n\n');
            setText('scenario-b-paper-text-history', text_history);
            const kg_history = (p.retrieval_history?.kg_channel || [])
                .map((s, i) => `Sub-query ${i + 1}: ${s.sub_query}\nAnswer: ${s.answer}`)
                .join('\n\n');
            setText('scenario-b-paper-kg-history', kg_history);
        }
    }

    async function consumeSseStream(response, onEvent) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // Parse SSE message frames separated by blank lines.
            let idx;
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
                const frame = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                let event = 'message';
                let data = '';
                for (const line of frame.split('\n')) {
                    if (line.startsWith('event:')) event = line.slice(6).trim();
                    else if (line.startsWith('data:')) data += line.slice(5).trim();
                }
                if (event && data) {
                    try {
                        onEvent(event, JSON.parse(data));
                    } catch (e) {
                        console.warn('[scenario-b] SSE parse failed:', e, frame);
                    }
                }
            }
        }
    }

    async function runScenarioB() {
        const sid = scenarioBState.selectedScenarioId;
        const ticker = scenarioBState.selectedTicker;
        if (!sid || !ticker) {
            setStatus('Select scenario and ticker first.');
            return;
        }
        const runBtn = document.getElementById('scenario-b-run-btn');
        const analyzeBtn = document.getElementById('scenario-b-analyze-btn');
        if (runBtn) runBtn.disabled = true;
        if (analyzeBtn) analyzeBtn.hidden = true;
        clearProgress();
        setText('scenario-b-agentic-answer', '—');
        setText('scenario-b-paper-answer', '—');
        setStatus('Running both panes…');

        const panes = ['agentic_dense', 'paper_mirror'];
        try {
            const res = await fetch('/api/scenario-b/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scenario_id: sid, ticker, panes,
                    enable_self_reflection: true,
                }),
            });
            if (!res.ok) {
                setStatus(`HTTP ${res.status}`);
                return;
            }
            await consumeSseStream(res, (eventName, payload) => {
                if (eventName === 'stage') {
                    const stage = payload.stage || '';
                    const pane = payload.pane;
                    if (pane) appendStagePill(pane, stage, 'done');
                } else if (eventName === 'stage_error') {
                    setStatus(`Stage error: ${payload.message || ''}`);
                } else if (eventName === 'all_done') {
                    scenarioBState.lastResolvedQuestion = payload.resolved_question || '';
                    scenarioBState.lastPaneOutputs = payload.pane_outputs || {};
                    renderPaneOutputs(scenarioBState.lastPaneOutputs);
                    if (analyzeBtn) analyzeBtn.hidden = false;
                    setStatus('Done.');
                } else if (eventName === 'error') {
                    setStatus(`Error: ${payload.message || ''}`);
                }
            });
        } catch (e) {
            setStatus(`Run failed: ${e.message}`);
        } finally {
            if (runBtn) runBtn.disabled = false;
        }
    }

    async function analyzeScenarioB() {
        const sid = scenarioBState.selectedScenarioId;
        const ticker = scenarioBState.selectedTicker;
        const paneOutputs = scenarioBState.lastPaneOutputs;
        if (!sid || !ticker || !paneOutputs) {
            setStatus('Run a scenario first.');
            return;
        }
        setStatus('Computing F1 + judge…');
        try {
            const res = await fetch('/api/scenario-b/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scenario_id: sid,
                    ticker,
                    pane_outputs: paneOutputs,
                }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                setStatus(`Analyze error: ${data.error || res.status}`);
                return;
            }
            const verdict = document.getElementById('scenario-b-verdict');
            const f1Block = document.getElementById('scenario-b-f1-block');
            const rationaleBlock = document.getElementById('scenario-b-rationale-block');
            const recovery = document.getElementById('scenario-b-recovery');

            const f1Per = data.f1_per_pane || {};
            if (f1Block) {
                f1Block.innerHTML = '<strong>Illustrative F1 per pane:</strong>';
                for (const [pane, score] of Object.entries(f1Per)) {
                    const row = document.createElement('div');
                    row.className = 'f1-pane-row';
                    row.innerHTML = `
                        <span class="pane-name">${pane}</span>
                        <span class="f1-score">F1 ${score.f1.toFixed(2)}</span>
                        <span>P ${score.precision.toFixed(2)} / R ${score.recall.toFixed(2)}</span>
                        <span>n_gold=${score.n_gold} n_pred=${score.n_pred} (${score.f1_confidence})</span>
                    `;
                    f1Block.appendChild(row);
                }
                if (Object.keys(f1Per).length === 0) {
                    const note = document.createElement('div');
                    note.style.color = '#888';
                    note.textContent = 'No gold for this ticker × scenario — qualitative judge only.';
                    f1Block.appendChild(note);
                }
            }
            if (rationaleBlock) {
                rationaleBlock.innerHTML = '';
                for (const [pane, msg] of Object.entries(data.llm_rationale_per_pane || {})) {
                    const div = document.createElement('div');
                    div.innerHTML = `<strong>${pane}:</strong> ${msg}`;
                    rationaleBlock.appendChild(div);
                }
            }
            if (recovery) {
                if (data.recovery_narrative) {
                    recovery.textContent = data.recovery_narrative;
                    recovery.hidden = false;
                } else {
                    recovery.hidden = true;
                }
            }
            if (verdict) verdict.hidden = false;
            setStatus('Done.');
        } catch (e) {
            setStatus(`Analyze failed: ${e.message}`);
        }
    }

    function showAdvanced() {
        scenarioBState.showAdvanced = true;
        const panes = document.getElementById('scenario-b-panes');
        if (panes) panes.dataset.paneMode = 'three';
        document.querySelectorAll('.scenario-b-panes [data-pane-name="tool_agent"]').forEach(el => {
            el.hidden = false;
        });
        const btn = document.getElementById('scenario-b-show-advanced-btn');
        if (btn) btn.hidden = true;
    }

    // URL hash deep-link: /compare#scenario-b?template=...&ticker=...&autorun=1
    function applyHashDeepLink() {
        const hash = window.location.hash;
        if (!hash.startsWith('#scenario-b')) return;
        const qs = hash.indexOf('?');
        if (qs < 0) return;
        const params = new URLSearchParams(hash.slice(qs + 1));
        const tpl = params.get('template');
        const ticker = params.get('ticker');
        const autorun = params.get('autorun') === '1';

        if (tpl) {
            const picker = document.getElementById('scenario-b-template-picker');
            if (picker) picker.value = tpl;
            scenarioBState.selectedScenarioId = tpl;
        }
        if (ticker) {
            const picker = document.getElementById('scenario-b-ticker-picker');
            if (picker) picker.value = ticker;
            scenarioBState.selectedTicker = ticker;
        }
        refreshResolvedQuestion();
        if (autorun) {
            // 200ms delay so the user sees the form auto-fill (per VP-Prod #2).
            setTimeout(runScenarioB, 200);
        }
    }

    // Picker change handlers (Wave-E delegation: the radio is data-change-action).
    if (typeof registerChangeAction === 'function') {
        registerChangeAction('scenario-b-template-picker', (el) => {
            scenarioBState.selectedScenarioId = el.value || '';
            refreshResolvedQuestion();
        });
        registerChangeAction('scenario-b-ticker-picker', (el) => {
            scenarioBState.selectedTicker = el.value || '';
            refreshResolvedQuestion();
        });
    } else {
        // Fallback wiring if registerChangeAction isn't available.
        document.addEventListener('change', (e) => {
            const t = e.target;
            if (!t) return;
            if (t.id === 'scenario-b-template-picker') {
                scenarioBState.selectedScenarioId = t.value || '';
                refreshResolvedQuestion();
            } else if (t.id === 'scenario-b-ticker-picker') {
                scenarioBState.selectedTicker = t.value || '';
                refreshResolvedQuestion();
            }
        });
    }

    if (typeof registerAction === 'function') {
        registerAction('scenario-b-run', () => runScenarioB());
        registerAction('scenario-b-analyze', () => analyzeScenarioB());
        registerAction('scenario-b-show-advanced', () => showAdvanced());
    }

    async function init() {
        populateTickerPicker();
        await fetchTemplates();
        applyHashDeepLink();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
