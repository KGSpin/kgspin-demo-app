// scenario-b-runner.js — PRD-004 v5 Phase 5A fixup-20260430 commit 2.
//
// Modal-context-aware Multi-hop runner. Lives inside the per-graph
// modal Why tab (Multi-hop sub-tab). Reads ticker + domain from the
// active slot binding (no ticker picker, no domain picker — slot
// owns both). Picker shows scenarios filtered to the slot's domain;
// scaffold entries get a "(TBD)" suffix and disable Run when
// selected (per fixup F5). SSE consumed via fetch+ReadableStream
// so we can POST a JSON body (EventSource is GET-only).

(function () {
    'use strict';

    const scenarioBState = {
        templates: [],
        templatesByScenarioId: {},
        selectedScenarioId: '',
        lastResolvedQuestion: '',
        lastPaneOutputs: null,
        showAdvanced: false,
        lockedDomain: null,
    };
    window.scenarioBState = scenarioBState;

    // ----- Slot context (mirrors scenario-a-runner.js) ---------------------

    function getSlotDomain() {
        if (typeof expandedSlot !== 'undefined' && expandedSlot !== null
            && typeof slotState !== 'undefined' && slotState[expandedSlot]) {
            const slot = slotState[expandedSlot];
            const meta = (typeof PIPELINE_META !== 'undefined') ? PIPELINE_META[slot.pipeline] : null;
            if (meta && meta.domain) return meta.domain;
        }
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
        const el = document.getElementById('modal-scenario-b-status');
        if (el) el.textContent = msg || '';
    }
    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text || '';
    }

    // ----- Templates fetch + domain-filter ---------------------------------

    async function fetchTemplates() {
        try {
            const res = await fetch('/api/scenario-b/templates');
            if (!res.ok) return;
            const all = await res.json();
            scenarioBState.templates = all;
            scenarioBState.templatesByScenarioId = Object.fromEntries(
                all.map(t => [t.scenario_id, t])
            );
            // Share the template index with the Single-shot sub-tab's
            // runner so its picker prefill works without a second fetch.
            if (window.scenarioAState) {
                window.scenarioAState.templatesByScenarioId =
                    scenarioBState.templatesByScenarioId;
            }
        } catch (e) {
            console.warn('[scenario-b] template fetch failed:', e);
        }
    }

    function populatePickers() {
        const slotDomain = scenarioBState.lockedDomain;
        // Map domain casing: PIPELINE_META uses 'financial'/'clinical';
        // template YAML uses 'fin'/'clinical'. Normalize.
        const wantDomain = (slotDomain === 'clinical') ? 'clinical' : 'fin';

        const filtered = scenarioBState.templates.filter(t => t.domain === wantDomain);

        // Multi-hop labels carry difficulty + hop-count badges (relevant
        // to that flow). Single-shot labels strip those — the question
        // is the same template, but the operator runs it as a one-shot
        // RAG-vs-GraphRAG comparison and hop counts are misleading
        // metadata in that context.
        const populate = (pickerId, leadOption, includeBadges) => {
            const picker = document.getElementById(pickerId);
            if (!picker) return;
            picker.innerHTML = `<option value="">${leadOption}</option>`;
            for (const t of filtered) {
                const opt = document.createElement('option');
                opt.value = t.scenario_id;
                const isScaffold = (t.status === 'scaffold');
                const tbdSuffix = isScaffold ? '   (TBD)' : '';
                let label;
                if (includeBadges) {
                    const hopBadge = t.expected_hops ? ` · ${t.expected_hops}-hop` : '';
                    label = `[${t.expected_difficulty}${hopBadge}] ${prettyId(t.scenario_id)}${tbdSuffix}`;
                } else {
                    label = `${prettyId(t.scenario_id)}${tbdSuffix}`;
                }
                opt.textContent = label;
                opt.dataset.status = t.status || 'ready';
                picker.appendChild(opt);
            }
        };

        // Multi-hop sub-tab picker — keep badges (operator narrates
        // hop count + difficulty during multi-hop demos).
        populate('modal-scenario-b-template-picker', '— Pick a scenario —', true);
        // Single-shot sub-tab picker — same templates, prefill into
        // textarea, drop the multi-hop-specific badges.
        populate('modal-scenario-a-template-picker', '— Pick a templated scenario (optional) —', false);
    }

    function prettyId(scenario_id) {
        return scenario_id.replace(/_/g, ' ').replace(/\bv5\b/g, '');
    }

    // ----- Picker change + Run-disable on scaffold -------------------------

    function onTemplatePicked(el) {
        const sid = el.value || '';
        scenarioBState.selectedScenarioId = sid;
        const tpl = scenarioBState.templatesByScenarioId[sid];
        const wrap = document.getElementById('modal-scenario-b-resolved-question');
        const text = document.getElementById('modal-scenario-b-question-text');
        const goldBadge = document.getElementById('modal-scenario-b-gold-badge');
        const noGoldBadge = document.getElementById('modal-scenario-b-no-gold-badge');
        const runBtn = document.getElementById('modal-scenario-b-run-btn');

        if (!tpl) {
            if (wrap) wrap.hidden = true;
            if (runBtn) runBtn.disabled = false;
            setStatus('');
            return;
        }

        // Render the resolved-question preview using the slot's ticker.
        if (text) text.textContent = renderTemplatePreview(tpl);
        if (wrap) wrap.hidden = false;

        // Scaffold entries — disable Run + show helper-text.
        if (tpl.status === 'scaffold') {
            if (runBtn) runBtn.disabled = true;
            setStatus('Scenario design pending — clinical v0 in progress.');
            if (goldBadge) goldBadge.hidden = true;
            if (noGoldBadge) noGoldBadge.hidden = true;
            return;
        }

        if (runBtn) runBtn.disabled = false;
        setStatus('');
        // Gold-availability badges: simple lookup by (scenario_id, ticker).
        const ticker = getSlotTicker();
        const hasGold = goldAvailableFor(sid, ticker);
        if (goldBadge) goldBadge.hidden = !hasGold;
        if (noGoldBadge) noGoldBadge.hidden = hasGold;
    }

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

    function goldAvailableFor(scenarioId, ticker) {
        const FIN_GOLD = ['AAPL', 'JNJ'];
        const CLINICAL_GOLD = { stelara_adverse_events_cohort_v5: ['JNJ-Stelara'] };
        if (CLINICAL_GOLD[scenarioId]) return CLINICAL_GOLD[scenarioId].includes(ticker);
        return FIN_GOLD.includes(ticker);
    }

    // ----- SSE consumption + Run / Analyze ---------------------------------

    function appendStagePill(paneName, stageLabel, status) {
        const map = {
            agentic_dense: 'modal-scenario-b-agentic-progress',
            paper_mirror: 'modal-scenario-b-paper-progress',
        };
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
        for (const id of ['modal-scenario-b-agentic-progress', 'modal-scenario-b-paper-progress']) {
            const c = document.getElementById(id);
            if (c) c.innerHTML = '';
        }
    }

    function renderPaneOutputs(paneOutputs) {
        if (paneOutputs.agentic_dense) {
            const a = paneOutputs.agentic_dense;
            setText('modal-scenario-b-agentic-answer', a.final_answer);
            const trace = (a.decomposition_trace || []).map((q, i) => `${i + 1}. ${q}`).join('\n');
            setText('modal-scenario-b-agentic-trace', trace);
        }
        if (paneOutputs.paper_mirror) {
            const p = paneOutputs.paper_mirror;
            setText('modal-scenario-b-paper-answer', p.final_answer);
            const text_history = (p.retrieval_history && p.retrieval_history.text_channel || [])
                .map((s, i) => `Sub-query ${i + 1}: ${s.sub_query}\nAnswer: ${s.answer}`)
                .join('\n\n');
            setText('modal-scenario-b-paper-text-history', text_history);
            const kg_history = (p.retrieval_history && p.retrieval_history.kg_channel || [])
                .map((s, i) => `Sub-query ${i + 1}: ${s.sub_query}\nAnswer: ${s.answer}`)
                .join('\n\n');
            setText('modal-scenario-b-paper-kg-history', kg_history);
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
                    try { onEvent(event, JSON.parse(data)); }
                    catch (e) { console.warn('[scenario-b] SSE parse failed:', e, frame); }
                }
            }
        }
    }

    async function runScenarioB() {
        const sid = scenarioBState.selectedScenarioId;
        const ticker = getSlotTicker();
        if (!sid || !ticker) {
            setStatus('Pick a scenario first.');
            return;
        }
        // Scaffold guard (also fires when Run was somehow not disabled).
        const tpl = scenarioBState.templatesByScenarioId[sid];
        if (tpl && tpl.status === 'scaffold') {
            setStatus('Scenario design pending — clinical v0 in progress.');
            return;
        }
        const runBtn = document.getElementById('modal-scenario-b-run-btn');
        const analyzeBtn = document.getElementById('modal-scenario-b-analyze-btn');
        const placeholder = document.getElementById('modal-scenario-b-placeholder');
        const verdict = document.getElementById('modal-scenario-b-verdict');
        if (runBtn) runBtn.disabled = true;
        if (analyzeBtn) analyzeBtn.disabled = true;
        if (verdict) verdict.hidden = true;
        if (placeholder) placeholder.hidden = false;
        clearProgress();
        setText('modal-scenario-b-agentic-answer', '—');
        setText('modal-scenario-b-paper-answer', '—');
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
                    if (analyzeBtn) analyzeBtn.disabled = false;
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
        const ticker = getSlotTicker();
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
                body: JSON.stringify({ scenario_id: sid, ticker, pane_outputs: paneOutputs }),
            });
            const data = await res.json();
            if (!res.ok || data.error) {
                setStatus(`Analyze error: ${data.error || res.status}`);
                return;
            }
            const placeholder = document.getElementById('modal-scenario-b-placeholder');
            const verdict = document.getElementById('modal-scenario-b-verdict');
            const f1Block = document.getElementById('modal-scenario-b-f1-block');
            const rationaleBlock = document.getElementById('modal-scenario-b-rationale-block');
            const recovery = document.getElementById('modal-scenario-b-recovery');

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
            if (placeholder) placeholder.hidden = true;
            if (verdict) verdict.hidden = false;
            setStatus('Done.');
        } catch (e) {
            setStatus(`Analyze failed: ${e.message}`);
        }
    }

    function showAdvanced() {
        scenarioBState.showAdvanced = true;
        const panes = document.getElementById('modal-scenario-b-panes');
        if (panes) panes.dataset.paneMode = 'three';
        document.querySelectorAll('#modal-scenario-b-panes [data-pane-name="tool_agent"]').forEach(el => {
            el.hidden = false;
        });
        const btn = document.getElementById('modal-scenario-b-show-advanced-btn');
        if (btn) btn.hidden = true;
    }

    // ----- Modal Why-tab init (called by slots.js switchModalTab) ----------

    window.initModalScenarioB = async function initModalScenarioB() {
        const ticker = getSlotTicker();
        const domain = getSlotDomain();
        scenarioBState.lockedDomain = domain;
        scenarioBState.selectedScenarioId = '';
        scenarioBState.lastPaneOutputs = null;

        clearProgress();
        setText('modal-scenario-b-agentic-answer', '—');
        setText('modal-scenario-b-paper-answer', '—');
        const placeholder = document.getElementById('modal-scenario-b-placeholder');
        const verdict = document.getElementById('modal-scenario-b-verdict');
        if (placeholder) placeholder.hidden = false;
        if (verdict) verdict.hidden = true;
        const analyzeBtn = document.getElementById('modal-scenario-b-analyze-btn');
        if (analyzeBtn) analyzeBtn.disabled = true;
        const wrap = document.getElementById('modal-scenario-b-resolved-question');
        if (wrap) wrap.hidden = true;

        if (!ticker || !domain) {
            setStatus('No slot context — close and re-expand a slot from the page above.');
            const runBtn = document.getElementById('modal-scenario-b-run-btn');
            if (runBtn) runBtn.disabled = true;
            return;
        }
        setStatus('');

        // Lazy-fetch templates (cached on first call).
        if (scenarioBState.templates.length === 0) {
            await fetchTemplates();
        }
        populatePickers();
    };

    // Picker change handler.
    if (typeof registerChangeAction === 'function') {
        registerChangeAction('modal-scenario-b-template-picker', (el) => onTemplatePicked(el));
    } else {
        document.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'modal-scenario-b-template-picker') {
                onTemplatePicked(e.target);
            }
        });
    }

    if (typeof registerAction === 'function') {
        registerAction('modal-scenario-b-run', () => runScenarioB());
        registerAction('modal-scenario-b-analyze', () => analyzeScenarioB());
        registerAction('modal-scenario-b-show-advanced', () => showAdvanced());
    }
})();
