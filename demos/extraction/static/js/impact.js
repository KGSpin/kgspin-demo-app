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
    // The 'agentic' sub-tab + cached-Q&A-run navigation are removed in
    // fixup-20260430 commit 6 (F8); the Single-shot Q&A sub-tab inside
    // each slot's modal Why tab (scenario-a-runner.js) replaces it.
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
    const degenerate = isDegenerateLineageSentence(match.sentence_text);
    const contextHtml = degenerate
        ? renderLineageContextHtml(lineageEvidenceIndex, match.chunk_id, match.sentence_index)
        : '';
    card.innerHTML = `
        <div style="display:flex; gap:16px; flex-wrap:wrap;">
            <div><strong>Method:</strong> ${methodLabel}</div>
            <div><strong>Confidence:</strong> ${(match.confidence * 100).toFixed(0)}%</div>
            ${match.fingerprint_similarity != null ? `<div><strong>Similarity:</strong> ${(match.fingerprint_similarity * 100).toFixed(0)}%</div>` : ''}
            ${match.rationale_code ? `<div><strong>Rationale:</strong> ${match.rationale_code}</div>` : ''}
        </div>
        <div style="margin-top:6px; color:#5B9FE6; font-size:11px;">${match.chunk_id || ''} / sentence ${match.sentence_index >= 0 ? match.sentence_index : '?'}</div>
        ${contextHtml}`;
    card.style.display = 'block';

    document.getElementById('lineage-edge-info').textContent =
        `${match.subject} \u2014[${match.predicate}]\u2192 ${match.object}`;

    // Clear previous highlights
    const sourcePanel = document.getElementById('lineage-source-text');
    sourcePanel.querySelectorAll('.source-highlight').forEach(el => el.classList.remove('source-highlight'));

    // Primary: find paragraph tagged with this chunk_id + sentence_index.
    // When the sentence is degenerate, also collect \u00b12 neighbors in the same
    // chunk so the highlight isn't a lonely "1,990".
    let target = null;
    const extras = [];
    if (match.chunk_id && match.sentence_index >= 0) {
        const tagged = sourcePanel.querySelectorAll('.source-para[data-evidence]');
        for (const para of tagged) {
            try {
                const ev = JSON.parse(para.dataset.evidence);
                if (ev.some(e => e.c === match.chunk_id && e.s === match.sentence_index)) {
                    if (!target) target = para;
                } else if (degenerate && ev.some(e =>
                    e.c === match.chunk_id &&
                    e.s >= match.sentence_index - 2 &&
                    e.s <= match.sentence_index + 2 &&
                    e.s !== match.sentence_index)) {
                    extras.push(para);
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
        for (const p of extras) p.classList.add('source-highlight');
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
// Tab 3: Graph Impact — Agentic Q&A (DELETED in fixup-20260430
// commit 6 / F8). startImpact + addImpactQA + renderCachedQARun
// + formatAnswer + renderImpactMetrics + renderImpactQualityAnalysis
// + askAgenticQuestion all gone. Backend route /api/impact/* + the
// `start-impact` data-action stay alive until 5B per VP-Prod #4.
// `escapeHtml` kept below — it's a generic HTML-escape utility used
// by compare-runner.js and graph.js, not Agentic-Q&A-specific.
// ============================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// Wave E — impact.js action registrations
registerAction('switch-impact-subtab', (el) => switchImpactSubTab(el.dataset.subtab));


// --- Legend Filtering (Sprint 90 Task 9, refactored Sprint 91b: per-pipeline) ---

