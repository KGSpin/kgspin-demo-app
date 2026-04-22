// slots.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 4903-4929: loadTrials ---
async function loadTrials() {
    try {
        const resp = await fetch('/api/clinical-trials');
        const data = await resp.json();
        const sel = document.getElementById('trial-select');
        sel.innerHTML = '';
        // Sprint 05 Task 3: friendly empty-state when no trials are configured
        if (!data.trials || data.trials.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No clinical trials configured';
            opt.disabled = true;
            opt.selected = true;
            sel.appendChild(opt);
            return;
        }
        data.trials.forEach((t, i) => {
            const opt = document.createElement('option');
            opt.value = t.nct_id;
            opt.textContent = t.display;
            opt.title = t.title;
            if (i === 0) opt.selected = true;
            sel.appendChild(opt);
        });
    } catch(e) { console.warn('Failed to load trials:', e); }
}


// --- compare.html lines 7613-7619: PIPELINE_META ---
const PIPELINE_META = {
    'discovery_rapid':   { label: 'Rapid Discovery',  subtitle: 'Linguistic baseline · 0 tokens',        backend: 'kgenskills', strategy: 'discovery_rapid', isKgspin: true,  color: '#5ED68A', capability: 'Discovery', helpAnchor: 'discovery-rapid' },
    'discovery_deep':    { label: 'Deep Discovery',   subtitle: 'Neural-hybrid · 0 tokens',              backend: 'kgenskills', strategy: 'discovery_deep',  isKgspin: true,  color: '#5ED68A', capability: 'Discovery', helpAnchor: 'discovery-deep' },
    'fan_out':           { label: 'Signal Fan-out',   subtitle: 'Relation-first · 0 tokens',             backend: 'kgenskills', strategy: 'fan_out',         isKgspin: true,  color: '#5ED68A', capability: 'Fan-out',   helpAnchor: 'fan-out' },
    'agentic_flash':     { label: 'Agentic Flash',    subtitle: 'LLM single-prompt',                     backend: 'gemini',     strategy: '',                isKgspin: false, color: '#E74C3C', capability: 'Agentic',   helpAnchor: 'agentic-flash' },
    'agentic_analyst':   { label: 'Agentic Analyst',  subtitle: 'LLM multi-stage schema-aware',          backend: 'modular',    strategy: '',                isKgspin: false, color: '#F39C12', capability: 'Agentic',   helpAnchor: 'agentic-analyst' },
};

// --- compare.html lines 7620-7801: loadBundleOptions + openSlotHelp + onSlotPipelineChange + onSlotBundleChange + tryLoadCachedSlot ---

async function loadBundleOptions() {
    if (bundleOptions) return bundleOptions;
    try {
        const domain = currentDomain || 'financial';
        const res = await fetch(`/api/bundle-options?domain=${domain}`);
        bundleOptions = await res.json();
        return bundleOptions;
    } catch (e) {
        console.error('Failed to load bundle options:', e);
        return null;
    }
}

// INIT-001 Sprint 04: open the static help page anchored to the selected
// pipeline's section. Falls back to the TOC if no pipeline is selected yet.
function openSlotHelp(slotIdx) {
    const sel = document.getElementById(`slot-${slotIdx}-pipeline`);
    const pipelineKey = sel ? sel.value : '';
    const meta = pipelineKey ? PIPELINE_META[pipelineKey] : null;
    const anchor = meta && meta.helpAnchor ? `#${meta.helpAnchor}` : '';
    window.open(`/static/pipelines-help.html${anchor}`, '_blank');
}

async function onSlotPipelineChange(slotIdx) {
    const sel = document.getElementById(`slot-${slotIdx}-pipeline`);
    const pipelineKey = sel.value;
    const bundleSel = document.getElementById(`slot-${slotIdx}-bundle`);
    const subtitleEl = document.getElementById(`slot-${slotIdx}-subtitle`);
    const runBtn = document.getElementById(`slot-${slotIdx}-run`);
    const graphContainer = document.getElementById(`slot-${slotIdx}-graph`);

    // Clear previous graph data for this slot
    if (networks[`slot-${slotIdx}`]) {
        networks[`slot-${slotIdx}`].destroy();
        delete networks[`slot-${slotIdx}`];
    }
    slotState[slotIdx] = { pipeline: pipelineKey, bundle: '', strategy: '', visData: null, stats: null, kg: null };

    if (!pipelineKey) {
        bundleSel.style.display = 'none';
        subtitleEl.textContent = '';
        runBtn.style.display = 'none';
        graphContainer.innerHTML = '<div class="placeholder">Select a pipeline above</div>';
        document.getElementById(`slot-${slotIdx}-toolbar`).style.display = 'none';
        document.getElementById(`slot-${slotIdx}-stats`).innerHTML = '';
        updateAnalyzeButton();
        return;
    }

    const meta = PIPELINE_META[pipelineKey];
    subtitleEl.textContent = meta.subtitle;

    // Sprint 118: Show domain dropdown for ALL pipelines (KGSpin and LLM).
    // LLMs are domain-aware — they use entity types and relationship patterns
    // from the selected domain version in their prompts.
    bundleSel.style.display = '';
    const opts = await loadBundleOptions();
    if (opts) {
        bundleSel.innerHTML = '<option value="">— Domain —</option>';
        // Sprint 118: Prefer split domain bundles over legacy monolithic bundles
        if (opts.domains && opts.domains.length > 0) {
            opts.domains.forEach(d => {
                const opt = document.createElement('option');
                opt.value = d.domain_id;
                // Show the admin-registered domain id verbatim (e.g.
                // ``financial-v2`` / ``clinical-v2``). Prior
                // "Domain V<version>" template produced "Domain Vfinancial-v2"
                // — missing space + redundant prefix.
                opt.textContent = d.domain_id;
                bundleSel.appendChild(opt);
            });
            const autoSelect = opts.default_domain_id || (opts.domains.length > 0 ? opts.domains[0].domain_id : '');
            if (autoSelect) {
                bundleSel.value = autoSelect;
                slotState[slotIdx].bundle = autoSelect;
            }
        } else if (opts.bundles) {
            // Legacy fallback: filter by strategy
            const strategyFilter = meta.strategy;
            const filtered = currentDomain === 'clinical'
                ? opts.bundles
                : opts.bundles.filter(b => b.strategy === strategyFilter);
            filtered.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.bundle_id;
                opt.textContent = b.linguistic || b.bundle_id;
                bundleSel.appendChild(opt);
            });
            const defaultMatch = filtered.find(b => b.bundle_id === opts.default_bundle_id);
            const autoSelect = defaultMatch ? defaultMatch.bundle_id : (filtered.length > 0 ? filtered[0].bundle_id : '');
            if (autoSelect) {
                bundleSel.value = autoSelect;
                slotState[slotIdx].bundle = autoSelect;
            }
        }
    }
    slotState[slotIdx].strategy = meta.strategy;

    // Show run button and set its color
    runBtn.style.display = '';
    runBtn.style.background = '#1a1a2a';
    runBtn.style.border = `1px solid ${meta.color}44`;
    runBtn.style.color = meta.color;
    runBtn.style.padding = '4px 10px';
    runBtn.style.borderRadius = '4px';
    runBtn.style.fontSize = '11px';
    runBtn.style.cursor = 'pointer';

    // Check if a cached run exists for this pipeline+bundle combo
    await tryLoadCachedSlot(slotIdx);
}

async function onSlotBundleChange(slotIdx) {
    const bundleSel = document.getElementById(`slot-${slotIdx}-bundle`);
    slotState[slotIdx].bundle = bundleSel.value;
    if (bundleSel.value) {
        await tryLoadCachedSlot(slotIdx);
    }
}

async function tryLoadCachedSlot(slotIdx) {
    const slot = slotState[slotIdx];
    const meta = PIPELINE_META[slot.pipeline];
    if (!meta) return;

    // Get ticker from correct source
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;

    // For KGSpin, need a bundle selected before checking cache
    if (meta.isKgspin && !slot.bundle) return;

    const graphContainer = document.getElementById(`slot-${slotIdx}-graph`);
    graphContainer.innerHTML = '<div class="placeholder" style="color:#888;">Checking cache...</div>';

    try {
        const params = new URLSearchParams({ pipeline: meta.backend });
        if (slot.bundle) params.set('bundle', slot.bundle);
        if (slot.strategy) params.set('strategy', slot.strategy);
        const res = await fetch(`/api/slot-cache-check/${ticker}?${params}`);
        const data = await res.json();

        if (data.cached) {
            slot.visData = data.vis;
            slot.stats = data.stats || {};
            slot.kg = data.kg || null;
            slot.totalRuns = data.total_runs || 1;
            slot.currentRunIndex = 0;
            // Cached-failure replay — render the same red "Failed to
            // generate" panel the live SSE path does, pulling the reason
            // + message from the stored kg.error payload.
            if (slot.kg && slot.kg.status === 'failed') {
                const err = slot.kg.error || {};
                renderSlotFailure(slotIdx, err.reason || 'extraction_failed',
                                  err.message || 'Extraction failed',
                                  err.type || '');
                updateAnalyzeButton();
                updateSlotHistory(slotIdx);
                return;
            }
            if (data.vis) {
                renderGraph(`slot-${slotIdx}`, data.vis, data.stats || {});
            }
            updateAnalyzeButton();
            updateSlotHistory(slotIdx);
            return;
        }
    } catch (e) {
        console.warn('Cache check failed:', e);
    }

    // No cache — show placeholder
    graphContainer.innerHTML = '<div class="placeholder">Click Run to extract</div>';
    updateAnalyzeButton();
}


// --- compare.html lines 7802-8030: runSlot ---
function runSlot(slotIdx) {
    // Get ticker from correct source based on domain
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) {
        const focusEl = currentDomain === 'clinical'
            ? document.getElementById('trial-select')
            : document.getElementById('doc-id-input');
        if (focusEl) focusEl.focus();
        return;
    }

    const slot = slotState[slotIdx];
    const meta = PIPELINE_META[slot.pipeline];
    if (!meta) return;

    const corpusKb = document.getElementById('corpus-kb-select').value;
    const model = document.getElementById('model-select').value;
    const chunkSize = document.getElementById('chunk-size-select') ? document.getElementById('chunk-size-select').value : '12';

    let endpoint, params;

    if (currentDomain === 'clinical') {
        // Clinical: use clinical compare endpoint with force_refresh for single pipeline
        const forceRefreshMap = { 'kgenskills': 'kgen', 'modular': 'modular', 'gemini': 'gemini' };
        endpoint = `/api/compare-clinical/${ticker}`;
        params = new URLSearchParams({ force_refresh: forceRefreshMap[meta.backend] || '', model: model, chunk_size: chunkSize });
        if (meta.isKgspin && slot.bundle) {
            params.set('bundle', slot.bundle);
        }
    } else {
        // Financial: use individual refresh endpoints (GET SSE streams)
        const endpointMap = {
            'kgenskills': `/api/refresh-discovery/${ticker}`,
            'modular': `/api/refresh-agentic-analyst/${ticker}`,
            'gemini': `/api/refresh-agentic-flash/${ticker}`,
        };
        endpoint = endpointMap[meta.backend];
        if (!endpoint) return;
        params = new URLSearchParams({ corpus_kb: corpusKb, model: model, chunk_size: chunkSize });
        // Sprint 118: Send bundle (domain version) for ALL pipelines, not just KGSpin.
        // LLMs use entity types and relationship patterns from the domain config.
        if (slot.bundle) {
            params.set('bundle', slot.bundle);
            if (meta.isKgspin && slot.strategy) params.set('strategy', slot.strategy);
        }
    }

    const btn = document.getElementById(`slot-${slotIdx}-run`);
    const progressEl = document.getElementById(`slot-${slotIdx}-progress`);
    const progressLabel = document.getElementById(`slot-${slotIdx}-progress-label`);
    const progressBar = document.getElementById(`slot-${slotIdx}-progress-bar`);
    btn.disabled = true;
    btn.textContent = '\u23f3 Running...';
    progressEl.style.display = 'block';
    progressBar.style.width = '0%';
    progressLabel.textContent = `${meta.label}: Connecting...`;

    // Also show in execution timeline
    const stepKey = `slot-${slotIdx}-run`;
    document.getElementById('compare-timeline').style.display = 'block';
    addTimelineStep('compare', stepKey, `${meta.label}: Starting...`, 'running');

    // Use EventSource (GET SSE) — these are streaming endpoints
    const es = new EventSource(`${endpoint}?${params}`);

    // For clinical, the compare endpoint emits events for ALL pipelines.
    // Filter to only process events matching this slot's pipeline.
    const _isRelevantEvent = (d) => {
        // If event has a pipeline/step field, check it matches this slot's backend
        const evPipeline = d.pipeline || d.step;
        if (!evPipeline) return true;  // No pipeline info = shared event (resolve_ticker, fetch_sec, parse_text)
        return evPipeline === meta.backend;
    };

    es.addEventListener('step_start', (e) => {
        const d = JSON.parse(e.data);
        if (!_isRelevantEvent(d)) return;
        progressLabel.textContent = d.label || `${meta.label}: Processing...`;
        updateStepState('compare', stepKey, d.label || `${meta.label}: Processing...`, 'running');
    });

    es.addEventListener('step_progress', (e) => {
        const d = JSON.parse(e.data);
        if (!_isRelevantEvent(d)) return;
        progressLabel.textContent = d.label || `${meta.label}: Processing...`;
        if (d.progress !== undefined && d.total) {
            const pct = Math.round((d.progress / d.total) * 100);
            progressBar.style.width = `${pct}%`;
            updateStepProgress('compare', stepKey, d.progress, d.total, d.label);
        }
    });

    // Clinical endpoint also emits chunk_progress for KGSpin
    es.addEventListener('chunk_progress', (e) => {
        const d = JSON.parse(e.data);
        if (!_isRelevantEvent(d)) return;
        if (d.chunk_index !== undefined && d.total_chunks) {
            const pct = Math.round((d.chunk_index / d.total_chunks) * 100);
            progressBar.style.width = `${pct}%`;
            progressLabel.textContent = `${meta.label}: Chunk ${d.chunk_index}/${d.total_chunks}`;
            updateStepProgress('compare', stepKey, d.chunk_index, d.total_chunks, progressLabel.textContent);
        }
    });

    es.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        if (!_isRelevantEvent(d)) return;
        progressLabel.textContent = d.label || `${meta.label}: Complete`;
        progressBar.style.width = '100%';
    });

    es.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        if (!_isRelevantEvent(d)) return;
        if (d.vis) {
            slot.visData = d.vis;
            slot.stats = d.stats || {};
            slot.kg = d.kg || null;
            slot.totalRuns = d.total_runs || 1;
            slot.currentRunIndex = 0;
            renderGraph(`slot-${slotIdx}`, d.vis, d.stats || {});
            updateAnalyzeButton();
            updateSlotHistory(slotIdx);

            // Sprint 05 HITL-round-2: WTM moved into the expand modal as a
            // slot-scoped "Why" tab. Nothing to show inline here any more.

            // For clinical: the compare endpoint runs all 3 pipelines, so
            // we got our kg_ready — reset the button and close the stream
            // instead of waiting for the done event (which fires after ALL pipelines).
            if (currentDomain === 'clinical') {
                es.close();
                btn.disabled = false;
                btn.innerHTML = '&#9654; Run';
                progressEl.style.display = 'none';
                progressLabel.style.color = '';
                const durationMs = d.stats ? d.stats.duration_ms || 0 : 0;
                completeStep('compare', stepKey, `${meta.label}: Complete`, durationMs, slot.stats.tokens || 0);
            }
        }
    });

    es.addEventListener('error', (e) => {
        // Named SSE 'error' event from server (not connection error).
        // Sprint 05 Task 5: if the payload carries a `reason` field, render
        // an actionable hint inside the slot's graph container with a
        // README link when the reason is a known EDGAR_IDENTITY config issue.
        if (e.data) {
            const d = JSON.parse(e.data);
            if (!_isRelevantEvent(d)) return;
            progressLabel.textContent = d.message || `${meta.label}: Error`;
            progressLabel.style.color = '#E74C3C';
            updateStepState('compare', stepKey, d.message || 'Error', 'error');
            if (d.reason) {
                const container = document.getElementById(`slot-${slotIdx}-graph`);
                if (container) {
                    // Map reason → user-facing title + help copy.
                    // Wave 3 follow-up: LLM extraction failures get their
                    // own prominent "Failed to generate" headline so the
                    // demo point lands (Flash failing on a document over
                    // the model's context window is a feature, not a bug).
                    let title = 'Corpus fetch failed';
                    let helpLink = '';
                    if (d.reason === 'EDGAR_IDENTITY missing') {
                        helpLink = '<div style="margin-top:10px;"><a href="/static/pipelines-help.html#edgar-identity" target="_blank" style="color:#5B9FE6;">How to set EDGAR_IDENTITY &raquo;</a></div>';
                    } else if (d.reason === 'NCT not found') {
                        helpLink = '<div style="margin-top:10px; color:#8b949e; font-size:11px;">Try a different NCT ID from ClinicalTrials.gov.</div>';
                    } else if (d.reason === 'context_exceeded') {
                        title = 'Failed to generate';
                        helpLink = '<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">This document is larger than the model\'s context window. That\'s the point of this pipeline — a single-shot LLM call can\'t always fit the whole input. The deterministic pipelines on this page handle arbitrarily large documents because they work chunk-local without a global token budget.</div>';
                    } else if (d.reason === 'quota_exceeded') {
                        title = 'Failed to generate';
                        helpLink = '<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">The LLM provider rate-limited or ran out of quota. Wait a minute and retry, or switch to a different model tier in the dropdown.</div>';
                    } else if (d.reason === 'output_truncated') {
                        title = 'Failed to generate';
                        helpLink = '<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">The model hit its output token cap mid-response. The JSON was truncated and could not be parsed — typical on dense documents where the extraction output exceeds the model\'s output budget.</div>';
                    } else if (d.reason === 'safety_block') {
                        title = 'Failed to generate';
                        helpLink = '<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">The model\'s safety filters blocked the response. Try a different document or a different model.</div>';
                    } else if (d.reason === 'backend_unreachable') {
                        title = 'Failed to generate';
                        helpLink = '<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">The LLM provider timed out or was unreachable. Check network + try again.</div>';
                    } else if (d.reason === 'extraction_failed') {
                        title = 'Failed to generate';
                    }
                    const attempted = (d.attempted || []).join(', ');
                    const attemptedStr = attempted ? `<div style="margin-top:6px; color:#6e7681; font-size:10px;">Providers attempted: ${attempted}</div>` : '';
                    const errorType = d.error_type ? `<div style="margin-top:6px; color:#888; font-size:10px; font-family:monospace;">error_type: ${d.error_type}</div>` : '';
                    container.innerHTML = `
                        <div class="placeholder" style="color:#E74C3C; padding:24px; text-align:left;">
                            <div style="font-weight:700; margin-bottom:10px; font-size:16px;">${title}</div>
                            <div style="color:#c9d1d9; font-size:12px; font-family:monospace; background:#0a0a1a; padding:8px; border-radius:4px; border-left:3px solid #E74C3C;">${(d.message || 'Unknown error').replace(/</g, '&lt;')}</div>
                            ${helpLink}
                            ${errorType}
                            ${attemptedStr}
                        </div>`;
                }
            }
        }
    });

    es.addEventListener('done', (e) => {
        es.close();
        const d = JSON.parse(e.data);
        btn.disabled = false;
        btn.innerHTML = '&#9654; Run';
        progressEl.style.display = 'none';
        progressLabel.style.color = '';
        const durationMs = d.total_duration_ms || 0;
        completeStep('compare', stepKey, `${meta.label}: Complete`, durationMs, slot.stats.tokens || 0);
    });

    es.onerror = (err) => {
        console.error('Slot SSE error:', err);
        es.close();
        btn.disabled = false;
        btn.innerHTML = '&#9654; Run';
        progressEl.style.display = 'none';
        progressLabel.textContent = `${meta.label}: Connection error`;
        progressLabel.style.color = '#E74C3C';
        updateStepState('compare', stepKey, `${meta.label}: Connection error`, 'error');
    };
}


// --- compare.html lines 8044-8104: WTM_DEFAULT_QUESTIONS + showWhyThisMattersSection + triggerWhyThisMatters ---
const WTM_DEFAULT_QUESTIONS = {
    financial: "Which companies does this entity compete with, and what products or services does it offer? Are any executives mentioned in connection with acquisitions or divestitures?",
    clinical: "What drugs are being developed by the sponsoring companies, and what medical conditions do they treat? Are there any cross-company relationships visible in the data?",
};

// Sprint 05 HITL-round-2: legacy inline "Why This Matters" — replaced by
// the slot-scoped Why tab inside the expand modal. Kept as a no-op stub in
// case any stale handler still calls it during a hot reload.
function showWhyThisMattersSection() { /* no-op */ }

function triggerWhyThisMatters() {
    const ticker = document.getElementById('doc-id-input')?.value?.trim().toUpperCase();
    if (!ticker) return;

    const input = document.getElementById('wtm-question-input');
    const statusEl = document.getElementById('wtm-status');
    const answersEl = document.getElementById('wtm-answers');
    const withEl = document.getElementById('wtm-with-graph');
    const withoutEl = document.getElementById('wtm-without-graph');
    const withMeta = document.getElementById('wtm-with-meta');
    const withoutMeta = document.getElementById('wtm-without-meta');
    const runBtn = document.getElementById('wtm-run-btn');

    const question = input?.value?.trim();
    if (!question) { input?.focus(); return; }

    // Show answers area with spinners
    answersEl.style.display = 'grid';
    statusEl.textContent = 'Analyzing...';
    runBtn.disabled = true;
    runBtn.textContent = '...';
    withEl.innerHTML = '<div class="spinner" style="margin:20px auto;"></div>';
    withoutEl.innerHTML = '<div class="spinner" style="margin:20px auto;"></div>';

    const domain = currentDomain || 'financial';
    fetch(`/api/why-this-matters/${ticker}?domain=${domain}&question=${encodeURIComponent(question)}`)
        .then(r => r.json())
        .then(data => {
            runBtn.disabled = false;
            runBtn.textContent = 'Ask';
            if (data.error) {
                statusEl.textContent = 'Unavailable';
                withEl.innerHTML = `<span style="color:#f85149;">${data.error}</span>`;
                withoutEl.innerHTML = '';
                return;
            }
            withEl.innerHTML = (data.with_graph || 'No response').replace(/\n/g, '<br>');
            withoutEl.innerHTML = (data.without_graph || 'No response').replace(/\n/g, '<br>');
            withMeta.textContent = `${data.tokens_with || '?'} tokens · ${data.time_with_ms || '?'}ms · Source: ${data.graph_source || 'cached KG'}`;
            withoutMeta.textContent = `${data.tokens_without || '?'} tokens · ${data.time_without_ms || '?'}ms · Raw text only`;
            statusEl.textContent = 'Complete';
        })
        .catch(err => {
            runBtn.disabled = false;
            runBtn.textContent = 'Ask';
            statusEl.textContent = 'Error';
            withEl.innerHTML = `<span style="color:#f85149;">Failed to load: ${err.message}</span>`;
            withoutEl.innerHTML = '';
        });
}


// --- compare.html lines 8123-8186: updateSlotHistory + slotPrevRun + slotNextRun + loadSlotRun ---
function updateSlotHistory(slotIdx) {
    const slot = slotState[slotIdx];
    const historyEl = document.getElementById(`slot-${slotIdx}-history`);
    if (!slot.totalRuns || slot.totalRuns <= 0) {
        historyEl.style.display = 'none';
        return;
    }
    historyEl.style.display = '';
    const runLabel = document.getElementById(`slot-${slotIdx}-run-label`);
    runLabel.textContent = `Run ${slot.currentRunIndex + 1}/${slot.totalRuns}`;
    document.getElementById(`slot-${slotIdx}-prev`).disabled = (slot.currentRunIndex >= slot.totalRuns - 1);
    document.getElementById(`slot-${slotIdx}-next`).disabled = (slot.currentRunIndex <= 0);
}

async function slotPrevRun(slotIdx) {
    const slot = slotState[slotIdx];
    if (slot.currentRunIndex >= slot.totalRuns - 1) return;
    slot.currentRunIndex++;
    await loadSlotRun(slotIdx, slot.currentRunIndex);
}

async function slotNextRun(slotIdx) {
    const slot = slotState[slotIdx];
    if (slot.currentRunIndex <= 0) return;
    slot.currentRunIndex--;
    await loadSlotRun(slotIdx, slot.currentRunIndex);
}

async function loadSlotRun(slotIdx, runIndex) {
    const slot = slotState[slotIdx];
    const meta = PIPELINE_META[slot.pipeline];
    if (!meta) return;
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();

    // Map backend to run history endpoint
    const runsEndpointMap = {
        'kgenskills': `/api/kgen-runs/${ticker}/${runIndex}`,
        'modular': `/api/modular-runs/${ticker}/${runIndex}`,
        'gemini': `/api/gemini-runs/${ticker}/${runIndex}`,
    };
    const endpoint = runsEndpointMap[meta.backend];
    if (!endpoint) return;

    try {
        const res = await fetch(endpoint);
        const data = await res.json();
        if (data.vis) {
            slot.visData = data.vis;
            slot.stats = data.stats || {};
            slot.kg = data.kg || null;
            slot.totalRuns = data.total_runs || slot.totalRuns;
            renderGraph(`slot-${slotIdx}`, data.vis, data.stats || {});
            updateSlotHistory(slotIdx);
            updateAnalyzeButton();
        }
    } catch (err) {
        console.error(`Load slot ${slotIdx} run ${runIndex} error:`, err);
    }
}

// ============================================================
// Sprint 91: Expand Modal — Graph / Explorer / Lineage
// ============================================================


// --- compare.html lines 8187-8949: Expand modal + lineage + intelligence in modal ---
function openExpandModal(slotIdx) {
    const slot = slotState[slotIdx];
    if (!slot.pipeline) return;
    expandedSlot = slotIdx;
    const meta = PIPELINE_META[slot.pipeline];
    const modal = document.getElementById('expand-modal');
    document.getElementById('expand-modal-title').textContent = `${meta.label}${slot.bundle ? ' — ' + slot.bundle : ''}`;

    // Reset tabs
    modal.querySelectorAll('.modal-tab').forEach(t => t.classList.toggle('active', t.textContent === 'Graph'));
    modal.querySelectorAll('.modal-tab-content').forEach(c => c.classList.toggle('active', c.id === 'modal-graph-content'));

    // Render graph in modal with controls — register in global dictionaries
    const container = document.getElementById('modal-graph-container');
    container.innerHTML = '';
    modalDataCache = null;

    // Clean up previous modal graph registrations
    ['modal-graph','modal-explorer','modal-lineage'].forEach(k => {
        delete networks[k]; delete nodeDataSets[k]; delete edgeDataSets[k];
        delete nodeMetaMaps[k]; delete edgeMetaMaps[k]; delete legendFilters[k];
        delete physicsEnabled[k]; delete showDisconnected[k]; delete originalNodeColors[k];
        const s = document.getElementById(`${k}-search`);
        if (s) s.value = '';
        const c = document.getElementById(`${k}-search-count`);
        if (c) c.textContent = '';
        const btn = document.getElementById(`${k}-physics-btn`);
        if (btn) btn.classList.add('active');
        const el = document.getElementById(`${k}-legend`);
        if (el) el.innerHTML = '';
        const rl = document.getElementById(`${k}-rel-legend`);
        if (rl) rl.innerHTML = '';
    });
    document.getElementById('modal-explorer-toolbar').style.display = 'none';

    if (slot.visData && slot.visData.nodes && slot.visData.nodes.length > 0) {
        const nodes = new vis.DataSet(slot.visData.nodes);
        const edges = new vis.DataSet(slot.visData.edges);
        nodeDataSets['modal-graph'] = nodes;
        edgeDataSets['modal-graph'] = edges;
        const nMeta = {};
        slot.visData.nodes.forEach(n => { if (n.metadata) nMeta[n.id] = n.metadata; });
        nodeMetaMaps['modal-graph'] = nMeta;
        const eMeta = {};
        slot.visData.edges.forEach(e => { if (e.metadata) eMeta[e.id] = e.metadata; });
        edgeMetaMaps['modal-graph'] = eMeta;
        physicsEnabled['modal-graph'] = true;
        showDisconnected['modal-graph'] = false;

        const options = {
            nodes: { shape: 'dot', font: { color: '#ffffff', size: 12 }, borderWidth: 2 },
            edges: { color: { color: '#555', highlight: '#5B9FE6' }, arrows: { to: { enabled: true, scaleFactor: 0.5 } }, font: { color: '#aaa', size: 9, align: 'middle' } },
            physics: { enabled: true, solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -30, centralGravity: 0.005, springLength: 120 } },
            interaction: { hover: true, tooltipDelay: 200, multiselect: false },
        };
        if (modalNetwork) { modalNetwork.destroy(); modalNetwork = null; }
        modalNetwork = new vis.Network(container, { nodes, edges }, options);
        networks['modal-graph'] = modalNetwork;
        buildLegend('modal-graph', slot.visData);
    } else {
        container.innerHTML = '<div class="placeholder">No graph data available</div>';
    }

    // Reset explorer/lineage placeholders
    const explorerContainer = document.getElementById('modal-explorer-container');
    if (explorerContainer) {
        // Reset Intelligence pipeline UI
        const articleList = document.getElementById('modal-intel-article-list');
        if (articleList) articleList.innerHTML = '';
        const articleCount = document.getElementById('modal-intel-article-count');
        if (articleCount) articleCount.textContent = '';
        const intelGraph = document.getElementById('modal-intel-graph');
        if (intelGraph) intelGraph.innerHTML = '<div class="placeholder" style="padding:40px; text-align:center; color:#666;">Click "Run Intelligence" to analyze news articles related to this entity.</div>';
    }
    // Reset lineage tab
    const lineageWelcome = document.getElementById('modal-lineage-welcome');
    const lineageLoaded = document.getElementById('modal-lineage-loaded');
    if (lineageWelcome) lineageWelcome.style.display = '';
    if (lineageLoaded) lineageLoaded.style.display = 'none';

    modal.style.display = 'block';
    document.body.style.overflow = 'hidden';
}

function closeExpandModal() {
    const modal = document.getElementById('expand-modal');
    modal.style.display = 'none';
    document.body.style.overflow = '';
    if (modalNetwork) { modalNetwork.destroy(); modalNetwork = null; }
    if (modalLineageNetwork) { modalLineageNetwork.destroy(); modalLineageNetwork = null; }
    if (modalIntelNetwork) { modalIntelNetwork.destroy(); modalIntelNetwork = null; }
    if (modalIntelEventSource) { modalIntelEventSource.close(); modalIntelEventSource = null; }
    // Clean up global registrations
    ['modal-graph','modal-explorer','modal-lineage'].forEach(k => {
        delete networks[k]; delete nodeDataSets[k]; delete edgeDataSets[k];
        delete nodeMetaMaps[k]; delete edgeMetaMaps[k]; delete legendFilters[k];
        delete physicsEnabled[k]; delete showDisconnected[k]; delete originalNodeColors[k];
    });
    expandedSlot = null;
    modalDataCache = null;
}

// ============================================================
// Modal Data Tab — tabular entity/relationship listing
// ============================================================
let modalDataCache = null;

function loadModalData(slotIdx) {
    const slot = slotState[slotIdx];
    if (!slot.visData || modalDataCache === slotIdx) return;
    modalDataCache = slotIdx;

    const nodes = slot.visData.nodes || [];
    const edges = slot.visData.edges || [];

    // Store for filtering
    window._modalDataNodes = nodes;
    window._modalDataEdges = edges;

    filterModalData();
}

function filterModalData() {
    const nodes = window._modalDataNodes || [];
    const edges = window._modalDataEdges || [];
    const filter = (document.getElementById('modal-data-filter').value || '').toLowerCase().trim();
    const showEnts = document.getElementById('modal-data-show-ents').checked;
    const showRels = document.getElementById('modal-data-show-rels').checked;
    const container = document.getElementById('modal-data-table-container');

    let html = '';
    let entCount = 0, relCount = 0;

    if (showEnts) {
        const filtered = nodes.filter(n => {
            if (!filter) return true;
            const label = (n.label || '').toLowerCase();
            const meta = n.metadata || {};
            const type = (meta.entity_type || '').toLowerCase();
            return label.includes(filter) || type.includes(filter);
        });
        entCount = filtered.length;
        html += `<div class="modal-data-section-header">Entities (${entCount})</div>`;
        html += `<table class="modal-data-table"><thead><tr><th>Entity</th><th>Type</th><th>Sources</th></tr></thead><tbody>`;
        filtered.forEach((n, idx) => {
            const meta = n.metadata || {};
            const type = meta.entity_type || '—';
            const color = TYPE_COLORS[type] || '#AAA';
            const sources = (meta.sources || []).length;
            const isNoise = meta.is_noise ? ' <span style="color:#FF4444;font-size:10px;">(noise)</span>' : '';
            html += `<tr class="data-row-clickable" onclick="toggleDataDetail(this)"><td>${escapeHtml(n.label || '')}${isNoise}</td><td><span class="type-badge" style="background:${color}33;color:${color};">${escapeHtml(type)}</span></td><td style="color:#888;">${sources}</td></tr>`;
            html += `<tr class="data-detail-row" style="display:none;"><td colspan="3">${renderEntityDetail(n)}</td></tr>`;
        });
        html += `</tbody></table>`;
    }

    if (showRels) {
        const filtered = edges.filter(e => {
            if (!filter) return true;
            const label = (e.label || '').toLowerCase();
            const meta = e.metadata || {};
            const subj = (meta.subject_text || '').toLowerCase();
            const obj = (meta.object_text || '').toLowerCase();
            return label.includes(filter) || subj.includes(filter) || obj.includes(filter);
        });
        relCount = filtered.length;
        html += `<div class="modal-data-section-header">Relationships (${relCount})</div>`;
        html += `<table class="modal-data-table"><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Method</th></tr></thead><tbody>`;
        filtered.forEach((e, idx) => {
            const meta = e.metadata || {};
            const subj = meta.subject_text || e.from || '';
            const obj = meta.object_text || e.to || '';
            const pred = e.label || meta.predicate || '—';
            const method = (meta.extraction_method || '').replace(/_/g, ' ');
            const relColor = REL_COLORS[pred] || '#AAA';
            html += `<tr class="data-row-clickable" onclick="toggleDataDetail(this)"><td>${escapeHtml(subj)}</td><td><span class="type-badge" style="background:${relColor}33;color:${relColor};">${escapeHtml(pred)}</span></td><td>${escapeHtml(obj)}</td><td style="color:#888;font-size:11px;">${escapeHtml(method)}</td></tr>`;
            html += `<tr class="data-detail-row" style="display:none;"><td colspan="4">${renderRelDetail(e)}</td></tr>`;
        });
        html += `</tbody></table>`;
    }

    const countsEl = document.getElementById('modal-data-counts');
    if (countsEl) countsEl.textContent = `${entCount} entities, ${relCount} relationships`;

    container.innerHTML = html || '<p style="color:#666;font-size:13px;">No data to display.</p>';
}

function toggleDataDetail(row) {
    const detailRow = row.nextElementSibling;
    if (detailRow && detailRow.classList.contains('data-detail-row')) {
        const isOpen = detailRow.style.display !== 'none';
        detailRow.style.display = isOpen ? 'none' : 'table-row';
        row.classList.toggle('data-row-expanded', !isOpen);
    }
}

function renderEntityDetail(node) {
    const meta = node.metadata || {};
    let html = '<div class="data-detail-panel">';
    html += `<div class="detail-field"><span class="detail-label">Label</span><span class="detail-value">${escapeHtml(node.label || '')}</span></div>`;
    html += `<div class="detail-field"><span class="detail-label">Type</span><span class="detail-value">${escapeHtml(meta.entity_type || '—')}</span></div>`;
    if (meta.is_noise) html += `<div class="detail-field"><span class="detail-label">Noise</span><span class="detail-value" style="color:#FF4444;">Yes</span></div>`;
    if (meta.text) html += `<div class="detail-field"><span class="detail-label">Text</span><span class="detail-value">${escapeHtml(meta.text)}</span></div>`;
    if (meta.sources && meta.sources.length > 0) {
        html += `<div class="detail-field"><span class="detail-label">Sources (${meta.sources.length})</span></div>`;
        meta.sources.forEach((src, i) => {
            if (typeof src === 'object') {
                html += `<div class="detail-source">`;
                if (src.sentence_text) html += `<div style="color:#ccc;font-size:12px;margin-bottom:2px;">"${escapeHtml(src.sentence_text)}"</div>`;
                const parts = [];
                if (src.chunk_id) parts.push(`chunk: ${src.chunk_id}`);
                if (src.sentence_index != null) parts.push(`sentence: ${src.sentence_index}`);
                if (src.confidence != null) parts.push(`confidence: ${(src.confidence * 100).toFixed(0)}%`);
                if (src.extraction_method) parts.push(`method: ${src.extraction_method.replace(/_/g,' ')}`);
                if (src.rationale_code) parts.push(`rationale: ${src.rationale_code}`);
                if (src.fingerprint_similarity != null) parts.push(`similarity: ${(src.fingerprint_similarity * 100).toFixed(0)}%`);
                if (parts.length) html += `<div style="color:#888;font-size:11px;">${parts.join(' | ')}</div>`;
                html += `</div>`;
            } else {
                html += `<div class="detail-source" style="color:#888;font-size:11px;">${escapeHtml(String(src))}</div>`;
            }
        });
    }
    // Show any additional metadata fields
    const skip = new Set(['entity_type','is_noise','text','sources']);
    for (const [k, v] of Object.entries(meta)) {
        if (skip.has(k) || v == null) continue;
        html += `<div class="detail-field"><span class="detail-label">${escapeHtml(k)}</span><span class="detail-value">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span></div>`;
    }
    html += '</div>';
    return html;
}

function renderRelDetail(edge) {
    const meta = edge.metadata || {};
    let html = '<div class="data-detail-panel">';
    html += `<div class="detail-field"><span class="detail-label">Subject</span><span class="detail-value">${escapeHtml(meta.subject_text || edge.from || '')}</span></div>`;
    html += `<div class="detail-field"><span class="detail-label">Predicate</span><span class="detail-value">${escapeHtml(meta.predicate || edge.label || '')}</span></div>`;
    html += `<div class="detail-field"><span class="detail-label">Object</span><span class="detail-value">${escapeHtml(meta.object_text || edge.to || '')}</span></div>`;
    if (meta.extraction_method) html += `<div class="detail-field"><span class="detail-label">Method</span><span class="detail-value">${escapeHtml(meta.extraction_method.replace(/_/g,' '))}</span></div>`;
    if (meta.confidence != null) html += `<div class="detail-field"><span class="detail-label">Confidence</span><span class="detail-value">${(meta.confidence * 100).toFixed(0)}%</span></div>`;
    if (meta.fingerprint_similarity != null) html += `<div class="detail-field"><span class="detail-label">Similarity</span><span class="detail-value">${(meta.fingerprint_similarity * 100).toFixed(0)}%</span></div>`;
    if (meta.rationale_code) html += `<div class="detail-field"><span class="detail-label">Rationale</span><span class="detail-value">${escapeHtml(meta.rationale_code)}</span></div>`;
    if (meta.sentence_text) html += `<div class="detail-field"><span class="detail-label">Source Sentence</span><span class="detail-value" style="font-style:italic;">"${escapeHtml(meta.sentence_text)}"</span></div>`;
    if (meta.chunk_id) html += `<div class="detail-field"><span class="detail-label">Chunk</span><span class="detail-value">${escapeHtml(meta.chunk_id)}${meta.sentence_index != null ? ' / sentence ' + meta.sentence_index : ''}</span></div>`;
    if (meta.source_document) html += `<div class="detail-field"><span class="detail-label">Source Document</span><span class="detail-value">${escapeHtml(meta.source_document)}</span></div>`;
    // Show any additional metadata fields
    const skip = new Set(['subject_text','object_text','predicate','extraction_method','confidence','fingerprint_similarity','rationale_code','sentence_text','chunk_id','sentence_index','source_document']);
    for (const [k, v] of Object.entries(meta)) {
        if (skip.has(k) || v == null) continue;
        html += `<div class="detail-field"><span class="detail-label">${escapeHtml(k)}</span><span class="detail-value">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span></div>`;
    }
    html += '</div>';
    return html;
}

function switchModalTab(tabName) {
    const modal = document.getElementById('expand-modal');
    modal.querySelectorAll('.modal-tab').forEach(t => {
        const name = t.textContent.toLowerCase();
        t.classList.toggle('active', name === tabName);
    });
    modal.querySelectorAll('.modal-tab-content').forEach(c => {
        const id = c.id.replace('modal-', '').replace('-content', '');
        c.classList.toggle('active', id === tabName);
    });

    // Lazy-load lineage when tab is clicked
    if (tabName === 'lineage' && expandedSlot !== null) {
        loadModalLineage(expandedSlot);
    }
    // Lazy-load explorer when tab is clicked
    if (tabName === 'explorer' && expandedSlot !== null) {
        loadModalExplorer(expandedSlot);
    }
    // Lazy-load data tab when clicked
    if (tabName === 'data' && expandedSlot !== null) {
        loadModalData(expandedSlot);
    }
    // Sprint 05 HITL-round-2: initialize Why tab with slot-scoped label
    if (tabName === 'why' && expandedSlot !== null) {
        initModalWhyTab(expandedSlot);
    }
}

// Sprint 05 HITL-round-2: Why tab inside expand modal
function initModalWhyTab(slotIdx) {
    const slot = slotState[slotIdx];
    const meta = slot ? PIPELINE_META[slot.pipeline] : null;
    const labelEl = document.getElementById('modal-wtm-pipeline-label');
    const input = document.getElementById('modal-wtm-question-input');
    if (labelEl && meta) labelEl.textContent = meta.label;
    // Seed default question if input is empty
    if (input && !input.value.trim()) {
        const domain = currentDomain || 'financial';
        input.value = WTM_DEFAULT_QUESTIONS[domain] || WTM_DEFAULT_QUESTIONS.financial;
    }
}

function triggerModalWhyThisMatters() {
    if (expandedSlot === null) return;
    const slot = slotState[expandedSlot];
    const meta = slot ? PIPELINE_META[slot.pipeline] : null;
    if (!meta) return;

    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;

    const input = document.getElementById('modal-wtm-question-input');
    const statusEl = document.getElementById('modal-wtm-status');
    const answersEl = document.getElementById('modal-wtm-answers');
    const withEl = document.getElementById('modal-wtm-with-graph');
    const withoutEl = document.getElementById('modal-wtm-without-graph');
    const withMeta = document.getElementById('modal-wtm-with-meta');
    const withoutMeta = document.getElementById('modal-wtm-without-meta');
    const runBtn = document.getElementById('modal-wtm-run-btn');

    const question = input?.value?.trim();
    if (!question) { input?.focus(); return; }

    answersEl.style.display = 'grid';
    statusEl.textContent = 'Analyzing...';
    runBtn.disabled = true;
    runBtn.textContent = '...';
    withEl.innerHTML = '<div class="spinner" style="margin:20px auto;"></div>';
    withoutEl.innerHTML = '<div class="spinner" style="margin:20px auto;"></div>';

    const domain = currentDomain || 'financial';
    const pipelineParam = meta.backend;  // kgenskills | gemini | modular
    const url = `/api/why-this-matters/${ticker}?domain=${domain}&pipeline=${pipelineParam}&question=${encodeURIComponent(question)}`;
    fetch(url)
        .then(r => r.json())
        .then(data => {
            runBtn.disabled = false;
            runBtn.textContent = 'Ask';
            if (data.error) {
                statusEl.textContent = 'Unavailable';
                withEl.innerHTML = `<span style="color:#f85149;">${data.error}</span>`;
                withoutEl.innerHTML = '';
                return;
            }
            withEl.innerHTML = (data.with_graph || 'No response').replace(/\n/g, '<br>');
            withoutEl.innerHTML = (data.without_graph || 'No response').replace(/\n/g, '<br>');
            withMeta.textContent = `${data.tokens_with || '?'} tokens · ${data.time_with_ms || '?'}ms · Source: ${data.graph_source || 'cached KG'}`;
            withoutMeta.textContent = `${data.tokens_without || '?'} tokens · ${data.time_without_ms || '?'}ms · Raw text only`;
            statusEl.textContent = 'Complete';
        })
        .catch(err => {
            runBtn.disabled = false;
            runBtn.textContent = 'Ask';
            statusEl.textContent = 'Error';
            withEl.innerHTML = `<span style="color:#f85149;">Failed to load: ${err.message}</span>`;
            withoutEl.innerHTML = '';
        });
}

let modalLineageNetwork = null;
let modalLineageEvidenceIndex = [];

function loadModalLineage(slotIdx) {
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;

    const welcomeEl = document.getElementById('modal-lineage-welcome');
    const loadedEl = document.getElementById('modal-lineage-loaded');
    const loadingMsg = document.getElementById('modal-lineage-loading-msg');
    loadingMsg.textContent = 'Loading lineage data...';
    welcomeEl.style.display = 'block';
    loadedEl.style.display = 'none';

    const _lineageDomain = currentDomain || 'financial';
    // Sprint 05 HITL-round-2 fix: scope lineage to THIS slot's pipeline so
    // opening an agentic_flash slot's modal doesn't surface the KGSpin graph.
    const _slot = slotState[slotIdx];
    const _meta = _slot ? PIPELINE_META[_slot.pipeline] : null;
    const _pipelineParam = _meta ? _meta.backend : 'kgenskills';
    fetch(`/api/impact/lineage/${ticker}?domain=${_lineageDomain}&pipeline=${_pipelineParam}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                loadingMsg.innerHTML = `<span style="color:#FF6B6B;">${data.error}</span>`;
                return;
            }

            welcomeEl.style.display = 'none';
            loadedEl.style.display = 'block';

            // KPIs
            document.getElementById('modal-auditability-value').textContent = data.auditability_index + '%';
            document.getElementById('modal-traced-edges-value').textContent = `${data.traced_edges}/${data.total_edges}`;
            const methodNames = data.extraction_methods.map(m => m.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '));
            document.getElementById('modal-extraction-methods-value').textContent = methodNames.join(', ');

            modalLineageEvidenceIndex = data.evidence_index || [];
            renderModalLineageSourceText(data.source_text, modalLineageEvidenceIndex);
            renderModalLineageGraph(data.vis);
        })
        .catch(err => {
            loadingMsg.innerHTML = `<span style="color:#FF6B6B;">Failed: ${err.message}</span>`;
        });
}

function renderModalLineageSourceText(text, evidenceIndex) {
    const panel = document.getElementById('modal-lineage-source-text');
    const sentenceMap = {};
    for (const ev of evidenceIndex) {
        if (ev.sentence_text && ev.chunk_id) {
            const key = `${ev.chunk_id}:${ev.sentence_index}`;
            if (!sentenceMap[key]) sentenceMap[key] = ev.sentence_text;
        }
    }
    const normalise = s => s.replace(/\s+/g, ' ');
    const paragraphs = text.split('\n');
    let html = '';
    for (const para of paragraphs) {
        const trimmed = para.trim();
        if (!trimmed) continue;
        const normPara = normalise(trimmed);
        const matches = [];
        for (const [key, sentText] of Object.entries(sentenceMap)) {
            const needle = normalise(sentText).substring(0, 80);
            if (needle && normPara.includes(needle)) {
                const parts = key.split(':');
                matches.push({ c: parts[0], s: parseInt(parts[1]) || -1 });
            }
        }
        const attrs = matches.length
            ? ` data-evidence='${JSON.stringify(matches).replace(/'/g, "&#39;")}'`
            : '';
        html += `<p class="source-para"${attrs}>${escapeHtml(trimmed)}</p>`;
    }
    panel.innerHTML = html;
}

function renderModalLineageGraph(visData) {
    const container = document.getElementById('modal-lineage-graph');
    container.innerHTML = '';
    const nodes = new vis.DataSet(visData.nodes);
    const edges = new vis.DataSet(visData.edges);
    nodeDataSets['modal-lineage'] = nodes;
    edgeDataSets['modal-lineage'] = edges;
    const nMeta = {};
    visData.nodes.forEach(n => { if (n.metadata) nMeta[n.id] = n.metadata; });
    nodeMetaMaps['modal-lineage'] = nMeta;
    const eMeta = {};
    visData.edges.forEach(e => { if (e.metadata) eMeta[e.id] = e.metadata; });
    edgeMetaMaps['modal-lineage'] = eMeta;
    physicsEnabled['modal-lineage'] = true;
    showDisconnected['modal-lineage'] = false;
    const options = {
        layout: { randomSeed: 42 },
        physics: { barnesHut: { gravitationalConstant: -2000, centralGravity: 0.3, springLength: 120 }, stabilization: { iterations: 100 } },
        interaction: { hover: true, tooltipDelay: 100 },
        edges: { smooth: { type: 'continuous' } },
        nodes: { shape: 'dot', borderWidth: 2 },
    };
    if (modalLineageNetwork) { modalLineageNetwork.destroy(); modalLineageNetwork = null; }
    modalLineageNetwork = new vis.Network(container, { nodes, edges }, options);
    networks['modal-lineage'] = modalLineageNetwork;
    buildLegend('modal-lineage', visData);
    // Ensure graph fits after layout settles
    setTimeout(() => { if (modalLineageNetwork) modalLineageNetwork.fit(); }, 300);

    modalLineageNetwork.on('selectEdge', (params) => {
        if (params.edges.length === 1) {
            const edge = edges.get(params.edges[0]);
            if (edge && edge.metadata) modalHighlightSourceForEdge(edge.metadata);
        }
    });
    modalLineageNetwork.on('deselectEdge', () => { modalClearSourceHighlight(); });
    modalLineageNetwork.on('selectNode', (params) => {
        if (params.nodes.length === 1) {
            const node = nodes.get(params.nodes[0]);
            if (node && node.metadata) modalHighlightSourceForNode(node.metadata);
        }
    });
    modalLineageNetwork.on('deselectNode', () => { modalClearSourceHighlight(); });
}

function modalHighlightSourceForEdge(meta) {
    const match = modalLineageEvidenceIndex.find(e =>
        e.subject === meta.subject_text && e.predicate === meta.predicate && e.object === meta.object_text
    );
    if (!match || !match.sentence_text) {
        document.getElementById('modal-lineage-edge-info').textContent = 'No source evidence available';
        document.getElementById('modal-lineage-evidence-card').style.display = 'none';
        return;
    }
    const card = document.getElementById('modal-lineage-evidence-card');
    const methodLabel = (match.extraction_method || 'unknown').replace(/_/g, ' ');
    card.innerHTML = `
        <div style="display:flex; gap:16px; flex-wrap:wrap;">
            <div><strong>Method:</strong> ${methodLabel}</div>
            <div><strong>Confidence:</strong> ${(match.confidence * 100).toFixed(0)}%</div>
            ${match.fingerprint_similarity != null ? `<div><strong>Similarity:</strong> ${(match.fingerprint_similarity * 100).toFixed(0)}%</div>` : ''}
            ${match.rationale_code ? `<div><strong>Rationale:</strong> ${match.rationale_code}</div>` : ''}
        </div>
        <div style="margin-top:6px; color:#5B9FE6; font-size:11px;">${match.chunk_id || ''} / sentence ${match.sentence_index >= 0 ? match.sentence_index : '?'}</div>`;
    card.style.display = 'block';
    document.getElementById('modal-lineage-edge-info').textContent = `${match.subject} \u2014[${match.predicate}]\u2192 ${match.object}`;

    const sourcePanel = document.getElementById('modal-lineage-source-text');
    sourcePanel.querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));

    let target = null;
    if (match.chunk_id && match.sentence_index >= 0) {
        const tagged = sourcePanel.querySelectorAll('.source-para[data-evidence]');
        for (const para of tagged) {
            try {
                const ev = JSON.parse(para.dataset.evidence);
                if (ev.some(e => e.c === match.chunk_id && e.s === match.sentence_index)) { target = para; break; }
            } catch (_) {}
        }
    }
    if (!target && match.sentence_text) {
        const normalise = s => s.replace(/\s+/g, ' ');
        const needle = normalise(match.sentence_text).substring(0, 120);
        const paras = sourcePanel.querySelectorAll('.source-para');
        for (const para of paras) {
            if (normalise(para.textContent).includes(needle)) { target = para; break; }
        }
    }
    if (target) {
        target.classList.add('source-highlight');
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function modalHighlightSourceForNode(meta) {
    const sourcePanel = document.getElementById('modal-lineage-source-text');
    sourcePanel.querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));

    const entityText = meta.text || meta.name || '';
    if (!entityText) return;
    document.getElementById('modal-lineage-edge-info').textContent = `Entity: ${entityText}`;
    document.getElementById('modal-lineage-evidence-card').style.display = 'none';

    const normalise = s => s.replace(/\s+/g, ' ').toLowerCase();
    const needle = normalise(entityText);
    const paras = sourcePanel.querySelectorAll('.source-para');
    for (const para of paras) {
        if (normalise(para.textContent).includes(needle)) {
            para.classList.add('source-highlight');
            para.scrollIntoView({ behavior: 'smooth', block: 'center' });
            break;
        }
    }
}

function modalClearSourceHighlight() {
    document.getElementById('modal-lineage-source-text').querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));
    document.getElementById('modal-lineage-edge-info').textContent = 'Click a node or edge to view its source';
    document.getElementById('modal-lineage-evidence-card').style.display = 'none';
}

let modalIntelNetwork = null;
let modalIntelArticles = [];
let modalIntelEventSource = null;

function loadModalExplorer(slotIdx) {
    // Reset article list and graph
    const articleList = document.getElementById('modal-intel-article-list');
    const graphEl = document.getElementById('modal-intel-graph');
    const countEl = document.getElementById('modal-intel-article-count');
    articleList.innerHTML = '';
    countEl.textContent = '0 articles';
    modalIntelArticles = [];

    // Check if Intelligence pipeline already ran for this ticker — show cached result
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) {
        graphEl.innerHTML = '<div class="placeholder">No ticker loaded</div>';
        return;
    }

    // Try loading from cached Intelligence run
    fetch(`/api/intel-runs/${ticker}`)
        .then(r => r.json())
        .then(data => {
            if (data.total > 0) {
                // Load the latest cached Intelligence run
                return fetch(`/api/intel-runs/${ticker}/0`).then(r => r.json());
            }
            return null;
        })
        .then(runData => {
            if (runData && runData.vis) {
                graphEl.innerHTML = '';
                const nodes = new vis.DataSet(runData.vis.nodes);
                const edges = new vis.DataSet(runData.vis.edges);
                const options = {
                    nodes: { shape: 'dot', font: { color: '#ffffff', size: 12 }, borderWidth: 2 },
                    edges: { color: { color: '#555', highlight: '#5B9FE6' }, arrows: { to: { enabled: true, scaleFactor: 0.5 } }, font: { color: '#aaa', size: 9, align: 'middle' } },
                    physics: { enabled: true, solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -30, centralGravity: 0.005, springLength: 120 } },
                    interaction: { hover: true, tooltipDelay: 200 },
                };
                if (modalIntelNetwork) { modalIntelNetwork.destroy(); modalIntelNetwork = null; }
                nodeDataSets['modal-explorer'] = nodes;
                edgeDataSets['modal-explorer'] = edges;
                const nMeta = {};
                runData.vis.nodes.forEach(n => { if (n.metadata) nMeta[n.id] = n.metadata; });
                nodeMetaMaps['modal-explorer'] = nMeta;
                const eMeta = {};
                runData.vis.edges.forEach(e => { if (e.metadata) eMeta[e.id] = e.metadata; });
                edgeMetaMaps['modal-explorer'] = eMeta;
                physicsEnabled['modal-explorer'] = true;
                showDisconnected['modal-explorer'] = false;
                modalIntelNetwork = new vis.Network(graphEl, { nodes, edges }, options);
                networks['modal-explorer'] = modalIntelNetwork;
                buildLegend('modal-explorer', runData.vis);
                document.getElementById('modal-explorer-toolbar').style.display = 'flex';
                // Show article count from cached data
                const articleCount = (runData.stats || {}).article_count || 0;
                countEl.textContent = `${articleCount} articles (cached)`;
                document.getElementById('modal-intel-run-btn').innerHTML = '&#9654; Re-run Intelligence';
            } else {
                graphEl.innerHTML = '<div class="placeholder">Click "Run Intelligence" to fetch news articles and build the knowledge graph.</div>';
            }
        })
        .catch(() => {
            graphEl.innerHTML = '<div class="placeholder">Click "Run Intelligence" to fetch news articles and build the knowledge graph.</div>';
        });
}

function runModalIntelligence() {
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;

    const btn = document.getElementById('modal-intel-run-btn');
    const articleList = document.getElementById('modal-intel-article-list');
    const graphEl = document.getElementById('modal-intel-graph');
    const countEl = document.getElementById('modal-intel-article-count');

    btn.disabled = true;
    btn.textContent = '\u23f3 Running...';
    articleList.innerHTML = '';
    graphEl.innerHTML = '<div class="placeholder">Fetching news articles...</div>';
    modalIntelArticles = [];
    countEl.textContent = '0 articles';

    if (modalIntelEventSource) modalIntelEventSource.close();
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const model = document.getElementById('model-select').value;
    const _intelDomain = currentDomain || 'financial';
    modalIntelEventSource = new EventSource(`/api/intelligence/${ticker}?corpus_kb=${corpusKb}&model=${model}&domain=${_intelDomain}`);

    modalIntelEventSource.addEventListener('article_fetched', (e) => {
        const d = JSON.parse(e.data);
        const idx = modalIntelArticles.length;
        const sourceClass = (d.source || '').includes('sec') ? 'sec' : (d.source || '').includes('health') ? 'healthcare' : 'news';
        const sourceLabel = (d.source || '').includes('sec') ? 'SEC' : (d.source || '').includes('health') ? 'FDA' : 'NEWS';
        const item = document.createElement('div');
        item.className = 'intel-article-item';
        item.id = `modal-intel-article-${idx}`;
        item.innerHTML = `
            <div class="intel-article-title">${d.title || d.source || 'Article'}</div>
            <div class="intel-article-meta">
                <span class="intel-source-badge ${sourceClass}">${sourceLabel}</span>
                ${d.chars ? `<span>${(d.chars / 1000).toFixed(1)}K chars</span>` : ''}
                <span class="intel-article-status">${d.cached ? '&#x2713; cached' : ''}</span>
            </div>
            <div class="intel-article-progress" id="modal-intel-article-progress-${idx}">
                <div class="intel-article-progress-bar" id="modal-intel-article-bar-${idx}"></div>
            </div>
        `;
        articleList.appendChild(item);
        modalIntelArticles.push(d);
        countEl.textContent = `${modalIntelArticles.length} articles`;
    });

    modalIntelEventSource.addEventListener('article_extracted', (e) => {
        const d = JSON.parse(e.data);
        const item = document.getElementById(`modal-intel-article-${d.article_idx}`);
        if (item) {
            item.classList.add('done');
            const status = item.querySelector('.intel-article-status');
            if (status) status.innerHTML = `&#x2713; ${d.entities || 0} entities, ${d.relationships || 0} rels`;
            const progress = document.getElementById(`modal-intel-article-progress-${d.article_idx}`);
            if (progress) progress.style.display = 'none';
        }
    });

    modalIntelEventSource.addEventListener('article_progress', (e) => {
        const d = JSON.parse(e.data);
        const bar = document.getElementById(`modal-intel-article-bar-${d.article_idx}`);
        const progress = document.getElementById(`modal-intel-article-progress-${d.article_idx}`);
        if (bar && progress) {
            progress.classList.add('active');
            bar.style.width = `${Math.round((d.progress / d.total) * 100)}%`;
        }
    });

    modalIntelEventSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        graphEl.innerHTML = '';
        if (d.vis) {
            const nodes = new vis.DataSet(d.vis.nodes);
            const edges = new vis.DataSet(d.vis.edges);
            const options = {
                nodes: { shape: 'dot', font: { color: '#ffffff', size: 12 }, borderWidth: 2 },
                edges: { color: { color: '#555', highlight: '#5B9FE6' }, arrows: { to: { enabled: true, scaleFactor: 0.5 } }, font: { color: '#aaa', size: 9, align: 'middle' } },
                physics: { enabled: true, solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -30, centralGravity: 0.005, springLength: 120 } },
                interaction: { hover: true, tooltipDelay: 200 },
            };
            if (modalIntelNetwork) { modalIntelNetwork.destroy(); modalIntelNetwork = null; }
            nodeDataSets['modal-explorer'] = nodes;
            edgeDataSets['modal-explorer'] = edges;
            const nMeta2 = {};
            d.vis.nodes.forEach(n => { if (n.metadata) nMeta2[n.id] = n.metadata; });
            nodeMetaMaps['modal-explorer'] = nMeta2;
            const eMeta2 = {};
            d.vis.edges.forEach(e => { if (e.metadata) eMeta2[e.id] = e.metadata; });
            edgeMetaMaps['modal-explorer'] = eMeta2;
            physicsEnabled['modal-explorer'] = true;
            showDisconnected['modal-explorer'] = false;
            modalIntelNetwork = new vis.Network(graphEl, { nodes, edges }, options);
            networks['modal-explorer'] = modalIntelNetwork;
            buildLegend('modal-explorer', d.vis);
            document.getElementById('modal-explorer-toolbar').style.display = 'flex';
        }
    });

    modalIntelEventSource.addEventListener('error', (e) => {
        if (!e.data) {
            modalIntelEventSource.close();
            btn.disabled = false;
            btn.innerHTML = '&#9654; Run Intelligence';
        }
    });

    modalIntelEventSource.addEventListener('done', (e) => {
        modalIntelEventSource.close();
        modalIntelEventSource = null;
        btn.disabled = false;
        btn.innerHTML = '&#9654; Re-run Intelligence';
    });
}

// Wave E — slots.js action registrations
registerAction('open-expand-modal', (el) => openExpandModal(+el.dataset.slot));
registerAction('close-expand-modal', () => closeExpandModal());
registerAction('slot-pipeline-change', (el) => onSlotPipelineChange(+el.dataset.slot));
registerAction('slot-bundle-change', (el) => onSlotBundleChange(+el.dataset.slot));
registerAction('open-slot-help', (el) => openSlotHelp(+el.dataset.slot));
registerAction('run-slot', (el) => runSlot(+el.dataset.slot));
registerAction('slot-prev-run', (el) => slotPrevRun(+el.dataset.slot));
registerAction('slot-next-run', (el) => slotNextRun(+el.dataset.slot));
registerAction('switch-modal-tab', (el) => switchModalTab(el.dataset.modalTab));
registerAction('filter-modal-data', () => filterModalData());
registerAction('trigger-modal-why-this-matters', () => triggerModalWhyThisMatters());
registerAction('run-modal-intelligence', () => runModalIntelligence());


// ============================================================
// Sprint 91b: Restored Analysis — Componentized Renderers
// ============================================================

// Pricing constants (Gemini 2.0 Flash)

