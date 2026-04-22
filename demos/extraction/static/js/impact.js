// impact.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 5079-5119: switchImpactSubTab ---
function switchImpactSubTab(name) {
    document.querySelectorAll('.impact-subtab').forEach(t =>
        t.classList.toggle('active', t.dataset.subtab === name));
    document.querySelectorAll('.impact-subtab-content').forEach(c =>
        c.classList.toggle('active', c.id === `impact-sub-${name}`));
    // Auto-load reproducibility when switching to that sub-tab
    if (name === 'reproducibility') {
        const ticker = document.getElementById('doc-id-input').value.trim();
        if (ticker) loadReproducibility();
    }
    if (name === 'lineage') {
        const ticker = document.getElementById('doc-id-input').value.trim();
        if (ticker) loadLineage();
    }
    if (name === 'agentic') {
        const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
        if (ticker && state.impact.results.length === 0) {
            fetch(`/api/impact-qa-runs/${ticker}`)
                .then(r => r.ok ? r.json() : null)
                .then(data => {
                    if (data && data.total > 0) {
                        qaRunTotal = data.total;
                        qaRunIndex = 0;
                        state.docId = ticker;
                        loadCachedQARun(ticker, 0).then(run => {
                            if (run) {
                                document.getElementById('agentic-welcome').style.display = 'none';
                                renderCachedQARun(run);
                                updateQARunNav();
                            }
                        });
                    }
                })
                .catch(() => {});
        }
    }
}

// ============================================================
// Compare Tab — Start Comparison
// ============================================================

// --- compare.html lines 6860-7536: Impact + lineage + repro + Q&A + askAgenticQuestion ---
let lineageNetwork = null;
let lineageEvidenceIndex = [];

async function loadLineage() {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase() || state.docId;
    if (!ticker) return;

    // Guard against redundant loads
    if (document.getElementById('lineage-content').style.display === 'block'
        && lineageEvidenceIndex.length > 0) {
        return;
    }

    const loadingMsg = document.getElementById('lineage-loading-msg');
    if (loadingMsg) loadingMsg.textContent = 'Loading lineage data...';

    try {
        const resp = await fetch(`/api/impact/lineage/${ticker}`);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: 'Run extraction first (Tab 1)' }));
            if (loadingMsg) loadingMsg.innerHTML = `<span style="color:#FF6B6B;">${err.error || 'Failed to load lineage data'}</span>`;
            return;
        }
        const data = await resp.json();

        document.getElementById('lineage-welcome').style.display = 'none';
        document.getElementById('lineage-content').style.display = 'block';

        // KPIs
        document.getElementById('auditability-value').textContent = data.auditability_index + '%';
        document.getElementById('traced-edges-value').textContent = `${data.traced_edges}/${data.total_edges}`;
        const methodNames = data.extraction_methods.map(m => m.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' '));
        const methodsEl = document.getElementById('extraction-methods-value');
        methodsEl.textContent = methodNames.join(', ');
        methodsEl.style.fontSize = '14px';

        lineageEvidenceIndex = data.evidence_index;
        renderLineageSourceText(data.source_text, data.evidence_index);
        renderLineageGraph(data.vis);
    } catch (e) {
        if (loadingMsg) loadingMsg.innerHTML = `<span style="color:#FF6B6B;">Failed: ${e.message}</span>`;
    }
}

function renderLineageSourceText(text, evidenceIndex) {
    const panel = document.getElementById('lineage-source-text');
    // Build a sentence-to-chunk lookup from evidence for tagging
    const sentenceMap = {};
    for (const ev of evidenceIndex) {
        if (ev.sentence_text && ev.chunk_id) {
            const key = `${ev.chunk_id}:${ev.sentence_index}`;
            if (!sentenceMap[key]) sentenceMap[key] = ev.sentence_text;
        }
    }

    // Render paragraphs — tag with ALL matching evidence (not just first)
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

function renderLineageGraph(visData) {
    const container = document.getElementById('lineage-graph');
    container.innerHTML = '';

    const nodes = new vis.DataSet(visData.nodes);
    const edges = new vis.DataSet(visData.edges);

    const options = {
        layout: { randomSeed: 42 },
        physics: {
            barnesHut: {
                gravitationalConstant: -2000,
                centralGravity: 0.3,
                springLength: 120,
            },
            stabilization: { iterations: 100 },
        },
        interaction: { hover: true, tooltipDelay: 100 },
        edges: { smooth: { type: 'continuous' } },
        nodes: { shape: 'dot', borderWidth: 2 },
    };

    lineageNetwork = new vis.Network(container, { nodes, edges }, options);

    lineageNetwork.on('selectEdge', (params) => {
        if (params.edges.length === 1) {
            const edge = edges.get(params.edges[0]);
            if (edge && edge.metadata) {
                highlightSourceForEdge(edge.metadata);
            }
        }
    });

    lineageNetwork.on('deselectEdge', () => {
        clearSourceHighlight();
    });

    lineageNetwork.on('selectNode', (params) => {
        if (params.nodes.length === 1) {
            const node = nodes.get(params.nodes[0]);
            if (node && node.metadata) {
                highlightSourceForNode(node.metadata);
            }
        }
    });

    lineageNetwork.on('deselectNode', () => {
        clearSourceHighlight();
    });
}

function highlightSourceForEdge(meta) {
    // Find matching evidence
    const match = lineageEvidenceIndex.find(e =>
        e.subject === meta.subject_text &&
        e.predicate === meta.predicate &&
        e.object === meta.object_text
    );

    if (!match || !match.sentence_text) {
        document.getElementById('lineage-edge-info').textContent = 'No source evidence available';
        document.getElementById('lineage-evidence-card').style.display = 'none';
        return;
    }

    // Show evidence card
    const card = document.getElementById('lineage-evidence-card');
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

    document.getElementById('lineage-edge-info').textContent =
        `${match.subject} \u2014[${match.predicate}]\u2192 ${match.object}`;

    // Clear previous highlights
    const sourcePanel = document.getElementById('lineage-source-text');
    sourcePanel.querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));

    // Primary: find paragraph tagged with this chunk_id + sentence_index
    let target = null;
    if (match.chunk_id && match.sentence_index >= 0) {
        const tagged = sourcePanel.querySelectorAll('.source-para[data-evidence]');
        for (const para of tagged) {
            try {
                const ev = JSON.parse(para.dataset.evidence);
                if (ev.some(e => e.c === match.chunk_id && e.s === match.sentence_index)) {
                    target = para;
                    break;
                }
            } catch (_) { /* skip malformed */ }
        }
    }

    // Fallback: normalised substring match (120 chars)
    if (!target && match.sentence_text) {
        const normalise = s => s.replace(/\s+/g, ' ');
        const needle = normalise(match.sentence_text).substring(0, 120);
        const paras = sourcePanel.querySelectorAll('.source-para');
        for (const para of paras) {
            if (normalise(para.textContent).includes(needle)) {
                target = para;
                break;
            }
        }
    }

    if (target) {
        target.classList.add('source-highlight');
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function highlightSourceForNode(meta) {
    const entityText = (meta.text || '').substring(0, 100);
    if (!entityText) return;

    // Try to find evidence mentioning this entity as subject or object
    const match = lineageEvidenceIndex.find(e =>
        e.subject === meta.text || e.object === meta.text
    );

    if (match && match.sentence_text) {
        // Reuse edge highlighting — synthesise the metadata shape it expects
        highlightSourceForEdge({
            subject_text: match.subject,
            predicate: match.predicate,
            object_text: match.object,
        });
        return;
    }

    // Fallback: search source text for entity name directly
    const sourcePanel = document.getElementById('lineage-source-scroll');
    sourcePanel.querySelectorAll('.source-highlight').forEach(el =>
        el.classList.remove('source-highlight'));

    // Show a minimal evidence card for the entity
    const card = document.getElementById('lineage-evidence-card');
    const entityType = meta.entity_type || 'Entity';
    const confidence = meta.confidence != null ? `${(meta.confidence * 100).toFixed(0)}%` : '--';
    card.innerHTML = `
        <div style="display:flex; gap:16px; flex-wrap:wrap;">
            <div><strong>Entity:</strong> ${escapeHtml(entityText)}</div>
            <div><strong>Type:</strong> ${entityType}</div>
            <div><strong>Confidence:</strong> ${confidence}</div>
        </div>
        <div style="margin-top:6px; color:#888; font-size:11px;">Showing first text occurrence</div>`;
    card.style.display = 'block';
    document.getElementById('lineage-edge-info').textContent = entityText;

    const normalise = s => s.replace(/\s+/g, ' ');
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

function clearSourceHighlight() {
    document.getElementById('lineage-evidence-card').style.display = 'none';
    document.getElementById('lineage-edge-info').textContent = 'Click a node or edge to view its source';
    document.querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));
}

// ============================================================
// Tab 3: Graph Impact — Reproducibility Benchmark
// ============================================================
async function loadReproducibility() {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase() || state.docId;
    if (!ticker) return;
    document.getElementById('repro-content').style.display = 'block';
    document.getElementById('repro-gauges').innerHTML = '<p style="color:#888; text-align:center;">Loading benchmark data...</p>';

    try {
        const corpusKb = document.getElementById('corpus-kb-select').value;
        const model = document.getElementById('model-select').value;
        const chunkSize = document.getElementById('chunk-size-select').value;
        const resp = await fetch(`/api/impact/reproducibility/${ticker}?corpus_kb=${corpusKb}&model=${model}&chunk_size=${chunkSize}`);
        if (!resp.ok) {
            document.getElementById('repro-gauges').innerHTML = '<p style="color:#FF6B6B; text-align:center;">Failed to load benchmark data</p>';
            return;
        }
        const data = await resp.json();
        renderReproGauges(data);
    } catch (e) {
        document.getElementById('repro-gauges').innerHTML = `<p style="color:#FF6B6B; text-align:center;">Error: ${e.message}</p>`;
    }
}

function renderReproGauges(data) {
    const container = document.getElementById('repro-gauges');

    function gaugeCard(label, result, cssClass) {
        if (result.deterministic) {
            return `<div class="repro-gauge ${cssClass}">
                <div class="gauge-label">${label}</div>
                <div class="gauge-value" style="color:#5ED68A">0%</div>
                <div class="gauge-bar"><div class="gauge-fill" style="width:100%"></div></div>
                <div class="gauge-kpi">100% Deterministic</div>
                <div class="gauge-detail">${result.num_runs} run(s) verified</div>
            </div>`;
        }
        if (result.insufficient) {
            return `<div class="repro-gauge ${cssClass}">
                <div class="gauge-label">${label}</div>
                <div class="gauge-value" style="color:#888">--</div>
                <div class="gauge-bar"><div class="gauge-fill" style="width:0%"></div></div>
                <div class="gauge-detail">${result.num_runs}/${2} runs needed <span class="audit-tooltip" data-tip="Only complete LLM runs are counted. Runs truncated due to context limits or dropped due to pipeline errors are excluded from reproducibility benchmarks.">&#9432;</span></div>
            </div>`;
        }
        const variance = result.variance_pct;
        const similarity = 100 - variance;
        const color = variance <= 5 ? '#5ED68A' : variance <= 20 ? '#FFE066' : '#FF6B6B';
        return `<div class="repro-gauge ${cssClass}">
            <div class="gauge-label">${label}</div>
            <div class="gauge-value" style="color:${color}">${variance}%</div>
            <div class="gauge-bar"><div class="gauge-fill" style="width:${similarity}%"></div></div>
            <div class="gauge-detail">
                Entities: ${result.avg_entity_similarity}% similar<br>
                Relationships: ${result.avg_rel_similarity}% similar<br>
                ${result.num_runs} runs compared
            </div>
        </div>`;
    }

    container.innerHTML =
        gaugeCard('KGSpin', data.kgen, 'kgen-gauge') +
        gaugeCard('LLM Full Shot', data.fullshot, 'llm-gauge') +
        gaugeCard('LLM Multi-Stage', data.modular, 'llm-gauge');

    if (data.needs_more_runs) {
        document.getElementById('repro-need-more').style.display = 'block';
    } else {
        document.getElementById('repro-need-more').style.display = 'none';
    }
}

// ============================================================
// Tab 3: Graph Impact — Agentic Q&A
// ============================================================
function startImpact() {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    if (!ticker) {
        document.getElementById('doc-id-input').focus();
        return;
    }
    state.docId = ticker;

    // Reset UI
    document.getElementById('impact-welcome').style.display = 'none';
    document.getElementById('agentic-welcome').style.display = 'none';
    document.getElementById('impact-timeline').style.display = 'block';
    document.getElementById('impact-content').style.display = 'block';
    document.getElementById('impact-metrics').innerHTML = '';
    document.getElementById('impact-qa-list').innerHTML = '';
    document.getElementById('impact-timeline-steps').innerHTML = '';
    const tl = tabTimeline.impact;
    tl.stepOrder.length = 0;
    for (const k in tl.stepElements) delete tl.stepElements[k];
    state.impact = { results: [], metrics: null };

    document.getElementById('status').textContent = 'Running impact analysis...';
    document.getElementById('impact-run-btn').disabled = true;
    const refreshBtn = document.getElementById('impact-refresh-btn');
    if (refreshBtn) refreshBtn.disabled = true;

    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/api/impact/${ticker}`);

    eventSource.addEventListener('step_start', (e) => {
        const d = JSON.parse(e.data);
        addTimelineStep('impact', d.step, d.label, 'running');
    });

    eventSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('impact', d.step, d.label, d.duration_ms, d.tokens);
    });

    eventSource.addEventListener('qa_result', (e) => {
        const d = JSON.parse(e.data);
        addImpactQA(d);
    });

    eventSource.addEventListener('impact_summary', (e) => {
        const d = JSON.parse(e.data);
        state.impact.metrics = d;
        renderImpactMetrics(d);
    });

    eventSource.addEventListener('impact_quality_analysis', (e) => {
        const d = JSON.parse(e.data);
        renderImpactQualityAnalysis(d);
    });

    eventSource.addEventListener('error', (e) => {
        if (e.data) {
            const d = JSON.parse(e.data);
            addTimelineStep('impact', d.step || 'error', d.message, 'error');
        } else {
            document.getElementById('status').textContent = 'Connection lost';
            eventSource.close();
        }
        document.getElementById('impact-run-btn').disabled = false;
    });

    eventSource.addEventListener('done', async (e) => {
        const d = JSON.parse(e.data);
        document.getElementById('status').textContent = `Complete (${(d.total_duration_ms / 1000).toFixed(1)}s)`;
        document.getElementById('impact-run-btn').disabled = false;
        const refreshBtn = document.getElementById('impact-refresh-btn');
        if (refreshBtn) refreshBtn.disabled = false;
        eventSource.close();

        // Refresh run navigation
        try {
            const resp = await fetch(`/api/impact-qa-runs/${state.docId}`);
            if (resp.ok) {
                const runs = await resp.json();
                qaRunTotal = runs.total;
                qaRunIndex = 0;
                if (qaRunTotal > 0) updateQARunNav();
            }
        } catch (_) {}
    });
}

function addImpactQA(d) {
    const list = document.getElementById('impact-qa-list');
    const card = document.createElement('div');
    card.className = 'impact-qa-card';
    const multihopBadge = d.is_multihop ? '<span class="multihop-badge">Multi-Hop</span>' : '';
    const timeWith = d.time_with_ms ? ` | ${d.time_with_ms}ms` : '';
    const timeWithout = d.time_without_ms ? ` | ${d.time_without_ms}ms` : '';

    function answerHtml(text, jsonData) {
        let textBlock;
        if (text) {
            textBlock = formatAnswer(text);
        } else if (jsonData) {
            // Sprint 100: Extract readable text from JSON when text field is missing.
            // Try common keys, then fall back to first long string value.
            const tryKeys = ['natural_language_response', 'answer', 'response', 'result', 'text', 'summary', 'analysis'];
            let extracted = null;
            for (const k of tryKeys) {
                if (jsonData[k] && typeof jsonData[k] === 'string') { extracted = jsonData[k]; break; }
            }
            if (!extracted) {
                // Find first string value longer than 50 chars
                for (const v of Object.values(jsonData)) {
                    if (typeof v === 'string' && v.length > 50) { extracted = v; break; }
                }
            }
            textBlock = extracted
                ? formatAnswer(extracted)
                : '<span style="color:#888; font-style:italic;">Structured data only — see JSON below</span>';
        } else {
            textBlock = '<span style="color:#888; font-style:italic;">No response</span>';
        }
        const jsonBlock = jsonData
            ? `<details class="qa-expandable"><summary>View JSON</summary>
                <pre>${escapeHtml(JSON.stringify(jsonData, null, 2))}</pre>
               </details>`
            : '';
        return textBlock + jsonBlock;
    }

    card.innerHTML = `
        <div class="impact-qa-question">Q: ${escapeHtml(d.question)} ${multihopBadge}</div>
        <div class="impact-qa-answers">
            <div class="impact-qa-answer">
                <div class="impact-qa-answer-header with-graph">
                    KG-Grounded RAG
                    <span class="token-count">${d.tokens_with || '?'} tokens${timeWith}</span>
                </div>
                <div>${answerHtml(d.with_graph, d.with_graph_json)}</div>
                <details class="qa-expandable"><summary>View Prompt</summary>
                    <pre>${escapeHtml(d.prompt_with || '')}</pre>
                </details>
            </div>
            <div class="impact-qa-answer">
                <div class="impact-qa-answer-header without-graph">
                    Vector RAG (Raw Text)
                    <span class="token-count">${d.tokens_without || '?'} tokens${timeWithout}</span>
                </div>
                <div>${answerHtml(d.without_graph, d.without_graph_json)}</div>
                <details class="qa-expandable"><summary>View Prompt</summary>
                    <pre>${escapeHtml(d.prompt_without || '')}</pre>
                </details>
            </div>
        </div>
    `;
    list.appendChild(card);
    state.impact.results.push(d);
}

// Q&A run navigation
let qaRunIndex = 0;
let qaRunTotal = 0;

async function loadCachedQARun(ticker, index) {
    try {
        const resp = await fetch(`/api/impact-qa-runs/${ticker}/${index}`);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        return null;
    }
}

async function navigateQARun(delta) {
    const newIndex = qaRunIndex + delta;
    if (newIndex < 0 || newIndex >= qaRunTotal) return;
    const ticker = state.docId;
    if (!ticker) return;
    const data = await loadCachedQARun(ticker, newIndex);
    if (!data) return;
    qaRunIndex = newIndex;
    renderCachedQARun(data);
    updateQARunNav();
}

function renderCachedQARun(data) {
    document.getElementById('impact-content').style.display = 'block';
    document.getElementById('impact-qa-list').innerHTML = '';
    document.getElementById('impact-timeline').style.display = 'none';
    state.impact = { results: [], metrics: null };
    for (const r of (data.results || [])) {
        addImpactQA(r);
    }
    if (data.summary) {
        state.impact.metrics = data.summary;
        renderImpactMetrics(data.summary);
    }
    if (data.quality_analysis) {
        renderImpactQualityAnalysis(data.quality_analysis);
    }
}

async function updateQARunNav() {
    const nav = document.getElementById('agentic-run-nav');
    nav.style.display = 'flex';
    document.getElementById('agentic-prev-btn').disabled = (qaRunIndex <= 0);
    document.getElementById('agentic-next-btn').disabled = (qaRunIndex >= qaRunTotal - 1);

    if (qaRunTotal >= 2) {
        await showQAConsistency();
    } else {
        const label = document.getElementById('agentic-run-label');
        label.innerHTML = `Run ${qaRunIndex + 1} of ${qaRunTotal} &nbsp;|&nbsp; `
            + `<span style="color:#888;">Run at least 2x to see consistency scores</span>`;
    }
}

async function showQAConsistency() {
    const ticker = state.docId;
    const run0 = await loadCachedQARun(ticker, 0);
    const run1 = await loadCachedQARun(ticker, 1);
    if (!run0 || !run1) return;
    const results0 = run0.results || [];
    const results1 = run1.results || [];
    const minLen = Math.min(results0.length, results1.length);

    function computeConsistency(field) {
        let consistent = 0;
        for (let i = 0; i < minLen; i++) {
            const tokens0 = new Set((results0[i][field] || '').toLowerCase().split(/\W+/).filter(Boolean));
            const tokens1 = new Set((results1[i][field] || '').toLowerCase().split(/\W+/).filter(Boolean));
            const intersection = [...tokens0].filter(t => tokens1.has(t)).length;
            const union = new Set([...tokens0, ...tokens1]).size;
            const jaccard = union > 0 ? intersection / union : 1;
            if (jaccard > 0.6) consistent++;
        }
        return minLen > 0 ? Math.round(consistent / minLen * 100) : 100;
    }

    const kgPct = computeConsistency('with_graph');
    const ragPct = computeConsistency('without_graph');
    function colorFor(pct) { return pct >= 80 ? '#5ED68A' : pct >= 50 ? '#FFE066' : '#FF6B6B'; }

    const label = document.getElementById('agentic-run-label');
    label.innerHTML = `Run ${qaRunIndex + 1} of ${qaRunTotal} &nbsp;|&nbsp; `
        + `<span style="color:${colorFor(kgPct)}">KG: ${kgPct}%</span> &nbsp; `
        + `<span style="color:${colorFor(ragPct)}">RAG: ${ragPct}%</span> consistency`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatAnswer(text) {
    if (!text) return '<span style="color:#666">No answer</span>';
    return text.replace(/\n/g, '<br>');
}

function renderImpactMetrics(m) {
    const container = document.getElementById('impact-metrics');
    container.innerHTML = `
        <div class="impact-metric">
            <div class="value" style="color:#5B9FE6">${m.avg_tokens_with || '?'}</div>
            <div class="label">Avg Tokens (With Graph)</div>
        </div>
        <div class="impact-metric">
            <div class="value" style="color:#FFE066">${m.avg_tokens_without || '?'}</div>
            <div class="label">Avg Tokens (Without)</div>
        </div>
        <div class="impact-metric">
            <div class="value" style="color:#5ED68A">${m.token_savings || '?'}%</div>
            <div class="label">Token Savings</div>
        </div>
        <div class="impact-metric">
            <div class="value" style="color:#E088E5">${m.questions_answered || '?'}/${m.total_questions || '?'}</div>
            <div class="label">Questions Answered</div>
        </div>
    `;
}

function renderImpactQualityAnalysis(d) {
    const a = d.analysis || {};
    const container = document.getElementById('impact-qa-list');

    // Create analysis card
    const card = document.createElement('div');
    card.className = 'impact-qa-card';
    card.style.borderTop = '2px solid #5B9FE6';
    card.style.marginTop = '16px';

    let perQuestionHtml = '';
    if (a.per_question && Array.isArray(a.per_question)) {
        perQuestionHtml = a.per_question.map(pq => {
            const winColor = pq.winner === 'with_kg' ? '#5ED68A' : pq.winner === 'without_kg' ? '#FFE066' : '#AAA';
            const winLabel = pq.winner === 'with_kg' ? 'With KG' : pq.winner === 'without_kg' ? 'Without KG' : 'Tie';
            return `<div style="margin:4px 0;font-size:13px;">
                <strong>Q${pq.question_num}:</strong>
                <span style="color:${winColor}">${winLabel}</span>
                — ${pq.reason || ''}
            </div>`;
        }).join('');
    }

    let scoresHtml = '';
    if (a.scores) {
        scoresHtml = `<div style="display:flex;gap:16px;margin-top:8px;font-size:13px;">
            <div><strong>With KG:</strong> Precision: ${a.scores.with_kg_precision || '?'}, Citations: ${a.scores.with_kg_citations || '?'}</div>
            <div><strong>Without KG:</strong> Precision: ${a.scores.without_kg_precision || '?'}, Citations: ${a.scores.without_kg_citations || '?'}</div>
        </div>`;
    }

    let hallucinationHtml = '';
    if (a.hallucination_risk) {
        const hr = a.hallucination_risk;
        const withClass = hr.with_kg <= 30 ? 'hallucination-low' : hr.with_kg <= 60 ? 'hallucination-medium' : 'hallucination-high';
        const withoutClass = hr.without_kg <= 30 ? 'hallucination-low' : hr.without_kg <= 60 ? 'hallucination-medium' : 'hallucination-high';
        hallucinationHtml = `<div style="display:flex;gap:16px;margin-top:8px;font-size:13px;align-items:center;">
            <strong>Hallucination Risk</strong>
            <span style="color:#888;font-size:11px;">(Experimental)</span>
            <span class="hallucination-score ${withClass}">With KG: ${hr.with_kg}/100</span>
            <span class="hallucination-score ${withoutClass}">Without KG: ${hr.without_kg}/100</span>
        </div>
        ${hr.explanation ? `<div style="font-size:12px;color:#888;margin-top:4px;">${hr.explanation}</div>` : ''}`;
    }

    const overallColor = a.overall_winner === 'with_kg' ? '#5ED68A' : a.overall_winner === 'without_kg' ? '#FFE066' : '#AAA';
    const overallLabel = a.overall_winner === 'with_kg' ? 'KG-Grounded RAG' : a.overall_winner === 'without_kg' ? 'Vector RAG' : 'Tie';

    card.innerHTML = `
        <div class="impact-qa-question" style="color:#5B9FE6">Quality Analysis</div>
        <div style="padding:8px 12px;">
            <div style="font-size:14px;margin-bottom:8px;">${a.summary || 'No analysis available.'}</div>
            ${perQuestionHtml}
            ${scoresHtml}
            ${hallucinationHtml}
            <div style="margin-top:10px;font-size:14px;">
                <strong>Overall Winner:</strong>
                <span style="color:${overallColor};font-weight:bold;">${overallLabel}</span>
                ${a.overall_reason ? ' — ' + a.overall_reason : ''}
            </div>
        </div>
    `;
    container.appendChild(card);
}

// ======================== Sprint 90: New Functions ========================

// --- Settings Panel ---

// --- compare.html lines 9635-9673: askAgenticQuestion ---
function askAgenticQuestion() {
    const question = document.getElementById('agentic-question-input').value.trim();
    if (!question) return;
    const pipeline = document.getElementById('agentic-pipeline-select').value;
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    if (!ticker) { alert('Please enter a ticker and run an extraction first.'); return; }

    const resultsEl = document.getElementById('agentic-results');
    // Add question card
    const qCard = document.createElement('div');
    qCard.style.cssText = 'background:#1a2a3a; border:1px solid #3a5a7a; border-radius:8px; padding:12px;';
    qCard.innerHTML = `
        <div style="color:#5B9FE6; font-size:12px; font-weight:600;">You asked:</div>
        <div style="color:#ccc; font-size:13px; margin-top:4px;">${question}</div>
        <div style="color:#888; font-size:11px; margin-top:4px;">Pipeline: ${pipeline} | Ticker: ${ticker}</div>
        <div id="agentic-answer-${Date.now()}" style="margin-top:8px; color:#aaa;">
            <div class="spinner" style="display:inline-block;width:12px;height:12px;"></div> Thinking...
        </div>
    `;
    resultsEl.prepend(qCard);
    const answerEl = qCard.querySelector('[id^="agentic-answer-"]');

    document.getElementById('agentic-question-input').value = '';

    fetch(`/api/impact/${ticker}/qa`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question, pipeline, ticker}),
    })
    .then(r => r.json())
    .then(data => {
        answerEl.innerHTML = `<div style="color:#ccc; font-size:13px;">${data.answer || data.response || 'No answer available.'}</div>`;
    })
    .catch(err => {
        answerEl.innerHTML = `<div style="color:#ff6b6b;">Error: ${err.message}</div>`;
    });
}

// --- Legend Filtering (Sprint 90 Task 9, refactored Sprint 91b: per-pipeline) ---

