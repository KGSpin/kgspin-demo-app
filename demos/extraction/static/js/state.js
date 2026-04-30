// state.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// ============================================================
// Wave E — event delegation registry
// Replaces inline on*= attributes. Handlers register themselves
// via registerAction('kebab-name', (el, event) => ...). Markup
// opts in via data-action / data-change-action / data-input-action
// / data-enter-action. Modal overlays use data-close-on-backdrop.
// ============================================================
const __actionHandlers = {};
function registerAction(name, handler) {
    __actionHandlers[name] = handler;
}
function __dispatchAction(name, el, event) {
    const handler = __actionHandlers[name];
    if (handler) handler(el, event);
}
document.addEventListener('click', (e) => {
    const backdrop = e.target.closest('[data-close-on-backdrop]');
    if (backdrop && e.target === backdrop) {
        __dispatchAction(backdrop.dataset.closeOnBackdrop, backdrop, e);
        return;
    }
    const el = e.target.closest('[data-action]');
    if (!el) return;
    __dispatchAction(el.dataset.action, el, e);
});
document.addEventListener('change', (e) => {
    const el = e.target.closest('[data-change-action]');
    if (!el) return;
    __dispatchAction(el.dataset.changeAction, el, e);
});
document.addEventListener('input', (e) => {
    const el = e.target.closest('[data-input-action]');
    if (!el) return;
    __dispatchAction(el.dataset.inputAction, el, e);
});
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const el = e.target.closest('[data-enter-action]');
    if (!el) return;
    __dispatchAction(el.dataset.enterAction, el, e);
});

// --- compare.html lines 2420-2479: MODEL_PRICING + cost helpers + tooltip DOMContentLoaded ---
// ============================================================
// Sprint 33.18: Model pricing table and cost helpers
// ============================================================
const MODEL_PRICING = {
    "gemini-2.5-flash-lite": { input: 0.10, output: 0.40 },
    "gemini-2.5-flash":      { input: 0.30, output: 2.50 },
};

function getModelPricing() {
    const m = document.getElementById('model-select').value;
    return MODEL_PRICING[m] || MODEL_PRICING["gemini-2.5-flash-lite"];
}

function calcCost(tokens) {
    const p = getModelPricing();
    return (tokens * 0.8 * p.input + tokens * 0.2 * p.output) / 1_000_000;
}

function calcCostForModel(tokens, model) {
    const p = MODEL_PRICING[model] || getModelPricing();
    return (tokens * 0.8 * p.input + tokens * 0.2 * p.output) / 1_000_000;
}

function costRateLabel() {
    const p = getModelPricing();
    return `$${p.input}/1M in, $${p.output}/1M out`;
}

function costRateLabelForModel(model) {
    const p = MODEL_PRICING[model] || getModelPricing();
    return `$${p.input}/1M in, $${p.output}/1M out`;
}

// Set cost cell with formatted display + raw value for cost-vs-best comparison
function setCostCell(cellId, rawCost, annotation) {
    const el = document.getElementById(cellId);
    el.dataset.rawCost = rawCost;
    el.innerHTML = rawCost > 0 ? `${fmtCost(rawCost)} <small style="color:#888">@ ${annotation}</small>` : '--';
}

function updateCostPerGB(cellId, cost, actualKb) {
    let corpusKb = parseInt(document.getElementById('corpus-kb-select').value);
    if (!corpusKb) corpusKb = actualKb || 200;
    const costPerGb = cost * (1_048_576 / corpusKb);
    document.getElementById(cellId).innerHTML = costPerGb > 0 ? fmtCost(costPerGb) : '--';
}

// Update tooltip when model changes
document.addEventListener('DOMContentLoaded', () => {
    const sel = document.getElementById('model-select');
    if (sel) sel.addEventListener('change', () => {
        const tip = document.getElementById('cost-tooltip');
        if (tip) {
            const m = sel.value;
            const p = MODEL_PRICING[m] || MODEL_PRICING["gemini-2.5-flash-lite"];
            tip.setAttribute('data-tip', `${m}: $${p.input}/1M input, $${p.output}/1M output (80/20 ratio)`);
        }
    });
});


// --- compare.html lines 2484-2492: graph-state globals (networks, edgeDataSets, etc.) ---
const networks = {};         // pipeline -> vis.Network instance
const physicsEnabled = {};   // pipeline -> boolean
const showDisconnected = {}; // pipeline -> boolean (Sprint 33.15: singleton toggle)
const highlightedRel = {};   // pipeline -> currently highlighted rel type (or null)
const edgeDataSets = {};     // pipeline -> vis.DataSet of edges
const nodeDataSets = {};     // pipeline -> vis.DataSet of nodes
const nodeMetaMaps = {};     // pipeline -> { nodeId: metadata } (fallback if vis.js strips custom props)
const edgeMetaMaps = {};     // pipeline -> { edgeId: metadata }
let detailPipeline = null;   // which pipeline's detail panel is open

// --- compare.html lines 2493-2510: state, slotState, bundleOptions, expandedSlot, modalNetwork ---

const state = {
    ticker: null,
    compare: { kgs_kg: null, gem_kg: null, analysis: null, vis_kgs: null, vis_gem: null, vis_mod: null, stats_kgs: null, stats_gem: null, stats_mod: null, source: null },
    intelligence: { kg: null, articles: [], entities: [] },
    impact: { results: [], metrics: null },
};

// Sprint 91: Slot-based architecture
// Each slot stores: { pipeline, bundle, strategy, visData, stats, kg }
const slotState = [
    { pipeline: '', bundle: '', strategy: '', visData: null, stats: null, kg: null },
    { pipeline: '', bundle: '', strategy: '', visData: null, stats: null, kg: null },
    { pipeline: '', bundle: '', strategy: '', visData: null, stats: null, kg: null },
];
let bundleOptions = null; // cached response from /api/bundle-options
let expandedSlot = null;  // which slot is currently in the expand modal
let modalNetwork = null;  // vis.Network instance in the expand modal

// --- compare.html lines 2511-2525: tabTimeline + feedback globals ---

// Per-tab timeline state
const tabTimeline = {
    compare: { stepOrder: [], stepElements: {} },
    intelligence: { stepOrder: [], stepElements: {} },
    impact: { stepOrder: [], stepElements: {} },
};

// ============================================================
// Sprint 39 (PRD-042): HITL Feedback System
// ============================================================
const feedbackState = {};  // key: `${pipeline}_${edgeId}` → { type: 'fp'|'fn', feedbackId: '...' }
let bundlePredicates = null;  // Cached from /api/bundle/predicates
let fpModalContext = null;    // { pipeline, edgeId, meta }
let fnModalContext = null;    // { pipeline, edgeId, meta }

// --- compare.html lines 2526-2547: getFeedbackState, showToast ---

function getFeedbackState(pipeline, edgeId) {
    const entry = feedbackState[`${pipeline}_${edgeId}`];
    return entry ? entry.type : null;
}

function showToast(message, type) {
    const toast = document.createElement('div');
    const bg = type === 'fp' ? '#5a2a2a' : type === 'fn' ? '#3a3017' : '#2a2a4e';
    const fg = type === 'fp' ? '#ff6b6b' : type === 'fn' ? '#d4a017' : '#ccc';
    toast.style.cssText = `padding:10px 18px;background:${bg};color:${fg};border-radius:8px;font-size:13px;margin-top:8px;pointer-events:auto;opacity:0;transition:opacity 0.3s;border:1px solid ${fg}33;`;
    toast.textContent = message;
    document.getElementById('hitl-toast').appendChild(toast);
    requestAnimationFrame(() => toast.style.opacity = '1');
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3000);
}

// Sprint 119: Resolve pipeline identifier to backend type.
// Handles legacy names ('kgenskills', 'gemini', 'modular') and
// 5-slot names ('slot-0', 'slot-1', ...) by looking up PIPELINE_META.
// TODO (tech debt): Frontend should query backend for pipeline metadata
// instead of hardcoding names — this is the third time stale names broke HITL.

// --- compare.html lines 2548-2565: resolveBackendType ---
function resolveBackendType(pipeline) {
    // Legacy direct matches
    if (pipeline === 'kgenskills') return 'kgspin';
    if (pipeline === 'gemini' || pipeline === 'modular') return 'llm';
    // 5-slot system: look up from slotState → PIPELINE_META
    const slotMatch = pipeline.match(/^slot-(\d+)$/);
    if (slotMatch && typeof slotState !== 'undefined') {
        const idx = parseInt(slotMatch[1]);
        const slot = slotState[idx];
        if (slot && slot.pipeline && typeof PIPELINE_META !== 'undefined') {
            const meta = PIPELINE_META[slot.pipeline];
            if (meta) return meta.isKgspin ? 'kgspin' : 'llm';
        }
    }
    // Fallback: show both buttons (FP + FN)
    return 'unknown';
}


// --- compare.html lines 4041-4189: purgeCache, schema, expand panel, executePurge ---
// ============================================================
// Cache Management (Sprint 33.17 — WI-5: Selective Purge Modal)
// ============================================================
function purgeCache() {
    document.getElementById('purge-modal').style.display = 'flex';
}

function closePurgeModal() {
    document.getElementById('purge-modal').style.display = 'none';
}

// Prompt template modal
async function showPromptTemplate(pipeline) {
    const modal = document.getElementById('prompt-modal');
    const title = document.getElementById('prompt-modal-title');
    const content = document.getElementById('prompt-modal-content');
    const label = pipeline === 'agentic_flash' ? 'Agentic Flash' : 'Agentic Analyst';
    title.textContent = `${label} — Prompt Template`;
    content.textContent = 'Loading...';
    modal.style.display = 'flex';
    try {
        const domain = document.querySelector('#domain-select')?.value || 'financial';
        const res = await fetch(`/api/prompt-template/${pipeline}?domain=${domain}`);
        const data = await res.json();
        if (data.error) {
            content.textContent = `Error: ${data.error}`;
        } else {
            content.textContent = data.template;
        }
    } catch (e) {
        content.textContent = `Failed to load: ${e.message}`;
    }
}

function closePromptModal() {
    document.getElementById('prompt-modal').style.display = 'none';
}

// Schema toggle
let _schemaLoaded = false;
function toggleSchema() {
    const details = document.getElementById('schema-details');
    const toggle = document.getElementById('schema-toggle');
    if (details.style.display === 'none') {
        details.style.display = 'block';
        toggle.innerHTML = 'Hide target schema &uarr;';
        if (!_schemaLoaded) loadSchema();
    } else {
        details.style.display = 'none';
        toggle.innerHTML = 'Show target schema &darr;';
    }
}

async function loadSchema() {
    try {
        const res = await fetch('/api/extraction-schema');
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        // Build entity type hierarchy HTML
        const hierarchy = data.type_hierarchy || {};
        let entHtml = '';
        for (const [parent, info] of Object.entries(hierarchy)) {
            const color = TYPE_COLORS[parent] || '#5ED68A';
            entHtml += `<div style="margin:4px 0;"><span style="color:${color}; font-weight:bold;">${parent}</span>`;
            if (info.definition) entHtml += ` <span style="color:#666;">— ${info.definition}</span>`;
            const subs = info.subtypes || {};
            for (const [sub, subDef] of Object.entries(subs)) {
                const subColor = TYPE_COLORS[sub] || color;
                entHtml += `<div style="margin-left:16px; color:#aaa;">└─ <span style="color:${subColor};">${sub}</span>`;
                if (subDef) entHtml += ` <span style="color:#666;">— ${subDef}</span>`;
                entHtml += `</div>`;
            }
            entHtml += `</div>`;
        }
        document.getElementById('schema-entity-types').innerHTML = entHtml;

        // Build relationship list HTML
        const rels = data.relationships || [];
        let relHtml = '';
        for (const r of rels) {
            const color = REL_COLORS[r.name] || '#AAA';
            relHtml += `<div style="margin:3px 0;"><span style="color:${color}; font-weight:bold;">${r.name}</span>`;
            if (r.definition) relHtml += ` <span style="color:#666;">— ${r.definition}</span>`;
            relHtml += `</div>`;
        }
        document.getElementById('schema-relationships').innerHTML = relHtml;

        // Populate ACTOR_TYPES from schema
        ACTOR_TYPES = new Set(data.valid_entity_types || []);
        _schemaLoaded = true;
    } catch (e) {
        document.getElementById('schema-entity-types').textContent = `Error: ${e.message}`;
    }
}

// Expand/shrink graph panels
function toggleExpandPanel(panelId) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    const row = panel.parentElement;
    const siblings = Array.from(row.querySelectorAll('.graph-panel'));
    const isExpanded = panel.classList.contains('expanded');

    if (isExpanded) {
        // Shrink back
        panel.classList.remove('expanded');
        siblings.forEach(s => s.classList.remove('hidden-by-expand'));
        panel.querySelector('.expand-btn').innerHTML = '&#x26F6;';
        panel.querySelector('.expand-btn').title = 'Expand';
    } else {
        // Expand — hide siblings, stretch this one
        siblings.forEach(s => {
            if (s !== panel) s.classList.add('hidden-by-expand');
        });
        panel.classList.add('expanded');
        panel.querySelector('.expand-btn').innerHTML = '&#x2716;';
        panel.querySelector('.expand-btn').title = 'Shrink';
    }
    // Re-fit the graph after layout change
    const pipeline = panelId.replace('-panel', '');
    setTimeout(() => { if (networks[pipeline]) networks[pipeline].fit(); }, 100);
}

async function executePurge() {
    const checkboxes = document.querySelectorAll('#purge-modal input[type="checkbox"]:checked');
    const layers = Array.from(checkboxes).map(cb => cb.value);
    if (layers.length === 0) { closePurgeModal(); return; }
    try {
        const resp = await fetch('/api/purge-cache', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layers }),
        });
        const data = await resp.json();
        closePurgeModal();
        alert('Cache purged: ' + data.purged.join(', '));
        location.reload();
    } catch (e) {
        alert('Purge failed: ' + e.message);
    }
}

// ============================================================
// Entity & Relationship Colors
// ============================================================
// Valid entity types — loaded from backend via is_noise flag on each node.
// This set is populated dynamically from /api/extraction-schema on first schema load.
// Used only for legend rendering; the backend is the source of truth for noise classification.

// --- compare.html lines 4251-4327: gemRunState + nav + load ---

// ============================================================
// Gemini Run History State
// ============================================================
const gemRunState = { currentIndex: 0, totalRuns: 0, ticker: null };

function updateGemRunUI() {
    const bar = document.getElementById('gemini-history');
    if (gemRunState.totalRuns <= 0) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    const runNum = gemRunState.totalRuns - gemRunState.currentIndex;
    document.getElementById('gem-run-label').textContent =
        `Run ${runNum} of ${gemRunState.totalRuns}`;
    document.getElementById('gem-prev').disabled = (gemRunState.currentIndex >= gemRunState.totalRuns - 1);
    document.getElementById('gem-next').disabled = (gemRunState.currentIndex <= 0);
}

function gemPrevRun() {
    if (gemRunState.currentIndex < gemRunState.totalRuns - 1) {
        gemRunState.currentIndex++;
        loadGeminiRun(gemRunState.currentIndex);
    }
}

function gemNextRun() {
    if (gemRunState.currentIndex > 0) {
        gemRunState.currentIndex--;
        loadGeminiRun(gemRunState.currentIndex);
    }
}

async function loadGeminiRun(index) {
    try {
        const resp = await fetch(`/api/gemini-runs/${gemRunState.docId}/${index}`);
        if (!resp.ok) return;
        const data = await resp.json();

        // Update Gemini graph
        renderGraph('gemini', data.vis);
        document.getElementById('gemini-toolbar').style.display = 'flex';

        // Update stats
        const statsEl = document.getElementById('gemini-stats');
        statsEl.innerHTML = `${data.stats.entities} entities | ${data.stats.relationships} rels | ${data.stats.tokens.toLocaleString()} tokens | ${(data.stats.duration_ms / 1000).toFixed(1)}s`;

        // Update run label and metadata — Sprint 33.11: sync totalRuns from server
        if (typeof data.total_runs === 'number') {
            gemRunState.totalRuns = data.total_runs;
        }
        gemRunState.currentIndex = index;
        updateGemRunUI();
        const ts = data.created_at ? new Date(data.created_at).toLocaleString() : '';
        document.getElementById('gem-run-meta').textContent = ts ? `${data.model || ''} ${ts}` : '';

        // Update audit table for Full Shot column
        const tokens = data.stats.tokens || 0;
        const cost = calcCostForModel(tokens, data.model);
        document.getElementById('audit-fullshot-tokens').textContent = tokens.toLocaleString();
        setCostCell('audit-fullshot-cost', tokens > 0 ? cost : 0, costRateLabelForModel(data.model));
        document.getElementById('audit-fullshot-errors').textContent = data.stats.errors || 0;
        if (data.stats.duration_ms && data.stats.actual_kb) {
            document.getElementById('audit-fullshot-throughput').textContent = (data.stats.actual_kb / (data.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
        }

        // Update state + matrix + clear stale analysis
        state.compare.stats_gem = data.stats;
        updateComparisonMatrix();
        clearAnalysis();
        refreshScores();
    } catch (e) {
        console.warn('Failed to load Gemini run:', e);
    }
}


// --- compare.html lines 4411-4481: modRunState + nav + load ---
const modRunState = { currentIndex: 0, totalRuns: 0, ticker: null };

function updateModRunUI() {
    const bar = document.getElementById('modular-history');
    if (modRunState.totalRuns <= 0) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    const runNum = modRunState.totalRuns - modRunState.currentIndex;
    document.getElementById('mod-run-label').textContent =
        `Run ${runNum} of ${modRunState.totalRuns}`;
    document.getElementById('mod-prev').disabled = (modRunState.currentIndex >= modRunState.totalRuns - 1);
    document.getElementById('mod-next').disabled = (modRunState.currentIndex <= 0);
}

function modPrevRun() {
    if (modRunState.currentIndex < modRunState.totalRuns - 1) {
        modRunState.currentIndex++;
        loadModularRun(modRunState.currentIndex);
    }
}

function modNextRun() {
    if (modRunState.currentIndex > 0) {
        modRunState.currentIndex--;
        loadModularRun(modRunState.currentIndex);
    }
}

async function loadModularRun(index) {
    try {
        const resp = await fetch(`/api/modular-runs/${modRunState.docId}/${index}`);
        if (!resp.ok) return;
        const data = await resp.json();

        renderGraph('modular', data.vis);
        document.getElementById('modular-toolbar').style.display = 'flex';

        const statsEl = document.getElementById('modular-stats');
        statsEl.innerHTML = `${data.stats.entities} entities | ${data.stats.relationships} rels | ${data.stats.tokens.toLocaleString()} tokens | ${(data.stats.duration_ms / 1000).toFixed(1)}s`;

        // Sprint 33.11: sync totalRuns from server
        if (typeof data.total_runs === 'number') {
            modRunState.totalRuns = data.total_runs;
        }
        modRunState.currentIndex = index;
        updateModRunUI();
        const ts = data.created_at ? new Date(data.created_at).toLocaleString() : '';
        document.getElementById('mod-run-meta').textContent = ts ? `${data.model || ''} ${ts}` : '';

        // Update audit table for Multi-Stage column
        const tokens = data.stats.tokens || 0;
        const cost = calcCostForModel(tokens, data.model);
        document.getElementById('audit-multistage-tokens').textContent = tokens.toLocaleString();
        setCostCell('audit-multistage-cost', tokens > 0 ? cost : 0, costRateLabelForModel(data.model));
        document.getElementById('audit-multistage-errors').textContent = data.stats.errors || 0;
        if (data.stats.duration_ms && data.stats.actual_kb) {
            document.getElementById('audit-multistage-throughput').textContent = (data.stats.actual_kb / (data.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
        }

        // Update state + matrix + clear stale analysis
        state.compare.stats_mod = data.stats;
        updateComparisonMatrix();
        clearAnalysis();
        refreshScores();
    } catch (e) {
        console.warn('Failed to load modular run:', e);
    }
}


// --- compare.html lines 4594-4667: kgenRunState + nav + load ---
const kgenRunState = { currentIndex: 0, totalRuns: 0, ticker: null };

function updateKgenRunUI() {
    const bar = document.getElementById('kgen-history');
    if (kgenRunState.totalRuns <= 0) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    const runNum = kgenRunState.totalRuns - kgenRunState.currentIndex;
    document.getElementById('kgen-run-label').textContent =
        `Run ${runNum} of ${kgenRunState.totalRuns}`;
    document.getElementById('kgen-prev').disabled = (kgenRunState.currentIndex >= kgenRunState.totalRuns - 1);
    document.getElementById('kgen-next').disabled = (kgenRunState.currentIndex <= 0);
}

function kgenPrevRun() {
    if (kgenRunState.currentIndex < kgenRunState.totalRuns - 1) {
        kgenRunState.currentIndex++;
        loadKgenRun(kgenRunState.currentIndex);
    }
}

function kgenNextRun() {
    if (kgenRunState.currentIndex > 0) {
        kgenRunState.currentIndex--;
        loadKgenRun(kgenRunState.currentIndex);
    }
}

async function loadKgenRun(index) {
    try {
        const resp = await fetch(`/api/kgen-runs/${kgenRunState.docId}/${index}`);
        if (!resp.ok) return;
        const data = await resp.json();

        renderGraph('kgenskills', data.vis);
        document.getElementById('kgenskills-toolbar').style.display = 'flex';

        const statsEl = document.getElementById('kgenskills-stats');
        const bv = data.bundle_version || '1.0';
        const qc = data.stats.quarantine_count || 0;
        const qBadge = qc > 0 ? ` <span style="color:#FF6B6B; font-size:11px; margin-left:4px;" title="${qc} entities quarantined by precision sieve">&#128683; ${qc} quarantined</span>` : '';
        statsEl.innerHTML = `${data.stats.entities} entities | ${data.stats.relationships} rels | ${(data.stats.duration_ms / 1000).toFixed(1)}s${qBadge} <span style="color:#7B68EE; font-size:11px; margin-left:6px;">&#128230; ${bv}</span>`;
        if (data.stats.throughput_kb_sec) {
            statsEl.innerHTML += `<div style="color:#5ED68A; font-size:11px; margin-top:4px;">&#9889; ${data.stats.throughput_kb_sec.toFixed(1)} KB/sec</div>`;
        }

        // Update audit table
        document.getElementById('audit-kgen-tokens').textContent = '0';
        const cpuCost = data.stats.cpu_cost || 0;
        setCostCell('audit-kgen-cost', cpuCost, '$0.05/hr CPU');
        if (data.stats.duration_ms && data.stats.actual_kb) {
            document.getElementById('audit-kgen-throughput').textContent = (data.stats.actual_kb / (data.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
        }

        if (typeof data.total_runs === 'number') {
            kgenRunState.totalRuns = data.total_runs;
        }
        kgenRunState.currentIndex = index;
        updateKgenRunUI();
        const ts = data.created_at ? new Date(data.created_at).toLocaleString() : '';
        document.getElementById('kgen-run-meta').textContent = ts ? `deterministic ${ts}` : '';

        // Update state + matrix + clear stale analysis
        state.compare.stats_kgs = data.stats;
        updateComparisonMatrix();
        clearAnalysis();
        refreshScores();
    } catch (e) {
        console.warn('Failed to load KGSpin run:', e);
    }
}


// --- compare.html lines 4750-4803: intelRunState + nav + load ---
const intelRunState = { currentIndex: 0, totalRuns: 0, ticker: null };

function updateIntelRunUI() {
    const bar = document.getElementById('intel-history');
    if (intelRunState.totalRuns <= 0) {
        bar.style.display = 'none';
        return;
    }
    bar.style.display = 'flex';
    const runNum = intelRunState.totalRuns - intelRunState.currentIndex;
    document.getElementById('intel-run-label').textContent =
        `Run ${runNum} of ${intelRunState.totalRuns}`;
    document.getElementById('intel-prev').disabled = (intelRunState.currentIndex >= intelRunState.totalRuns - 1);
    document.getElementById('intel-next').disabled = (intelRunState.currentIndex <= 0);
}

function intelPrevRun() {
    if (intelRunState.currentIndex < intelRunState.totalRuns - 1) {
        intelRunState.currentIndex++;
        loadIntelRun(intelRunState.currentIndex);
    }
}

function intelNextRun() {
    if (intelRunState.currentIndex > 0) {
        intelRunState.currentIndex--;
        loadIntelRun(intelRunState.currentIndex);
    }
}

async function loadIntelRun(index) {
    try {
        const resp = await fetch(`/api/intel-runs/${intelRunState.docId}/${index}`);
        if (!resp.ok) return;
        const data = await resp.json();

        renderGraph('intelligence', data.vis);
        document.getElementById('intelligence-toolbar').style.display = 'flex';

        const statsEl = document.getElementById('intelligence-stats');
        statsEl.innerHTML = `${data.stats.entities} entities | ${data.stats.relationships} rels | ${(data.stats.duration_ms / 1000).toFixed(1)}s`;

        if (typeof data.total_runs === 'number') {
            intelRunState.totalRuns = data.total_runs;
        }
        intelRunState.currentIndex = index;
        updateIntelRunUI();
        const ts = data.created_at ? new Date(data.created_at).toLocaleString() : '';
        document.getElementById('intel-run-meta').textContent = ts ? `deterministic ${ts}` : '';
    } catch (e) {
        console.warn('Failed to load Intelligence run:', e);
    }
}


// --- compare.html lines 4812-4823: runActiveTab + section header ---
// ============================================================
function runActiveTab() {
    const activeTab = document.querySelector('.tab.active').dataset.tab;
    if (activeTab === 'compare') startComparison();
    else if (activeTab === 'intelligence') startIntelligence();
    // Impact tab no longer has a top-level Run handler since the
    // Agentic Q&A entrypoint was removed in fixup-20260430 commit 6 (F8).
    // Lineage/reproducibility sub-tabs auto-load via switchImpactSubTab.
}

// ============================================================
// Init
// ============================================================
// Sprint 77 Task 5a: Track current domain

// --- compare.html lines 5017-5045: init ---
async function init() {
    const resp = await fetch('/api/tickers');
    const tickers = await resp.json();
    const dl = document.getElementById('doc-id-list');
    for (const [ticker, name] of Object.entries(tickers)) {
        const opt = document.createElement('option');
        opt.value = ticker;
        opt.label = `${ticker} - ${name}`;
        dl.appendChild(opt);
    }
    // Load available bundles for default domain
    await loadBundles('financial');
    document.getElementById('doc-id-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') runActiveTab();
    });
    document.getElementById('doc-id-input').addEventListener('input', () => {
        const activeTab = document.querySelector('.tab.active').dataset.tab;
        if (activeTab === 'impact') {
            const ticker = document.getElementById('doc-id-input').value.trim();
            const reproActive = document.querySelector('.impact-subtab[data-subtab="reproducibility"]').classList.contains('active');
            if (ticker && reproActive) loadReproducibility();
        }
    });
}
init();

// ============================================================
// Tab Switching
// ============================================================

// --- compare.html lines 5046-5078: switchTab ---
function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    // Toggle tab content — Sprint 91: Compare + Flags
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    const targetTab = document.getElementById(`tab-${tabName}`);
    if (targetTab) targetTab.classList.add('active');

    // Sprint 33.13: Efficiency audit only visible on compare tab
    const audit = document.getElementById('efficiency-audit');
    if (audit) {
        if (tabName === 'compare') {
            if (audit.dataset.wasVisible === 'true') audit.style.display = 'block';
        } else {
            if (audit.style.display === 'block') audit.dataset.wasVisible = 'true';
            audit.style.display = 'none';
        }
    }

    // Sprint 91: Load stored feedback when switching to Flags tab, filtered by current ticker
    if (tabName === 'flags') {
        let ticker = '';
        if (currentDomain === 'clinical') {
            ticker = document.getElementById('trial-select').value || '';
        } else {
            ticker = (document.getElementById('doc-id-input').value || '').trim().toUpperCase();
        }
        const docFilter = document.getElementById('stored-feedback-doc-filter');
        if (docFilter && ticker) docFilter.value = ticker;
        loadStoredFeedback();
    }
}


// Wave E — state.js action registrations
registerAction('switch-tab', (el) => switchTab(el.dataset.tab));
registerAction('purge-cache', () => purgeCache());
registerAction('close-purge-modal', () => closePurgeModal());
registerAction('execute-purge', () => executePurge());
registerAction('toggle-schema', (_el, e) => { e.preventDefault(); toggleSchema(); });
registerAction('close-prompt-modal', () => closePromptModal());


// --- compare.html lines 5570-5642: addTimelineStep + updateStepState + updateStepProgress + completeStep ---
function addTimelineStep(tab, step, label, stepState) {
    const tl = tabTimeline[tab];
    if (tl.stepElements[step]) {
        updateStepState(tab, step, label, stepState);
        return;
    }
    const container = document.getElementById(`${tab}-timeline-steps`);
    const el = document.createElement('div');
    el.className = 'timeline-step';
    el.id = `${tab}-step-${step}`;
    const iconHtml = stepState === 'running'
        ? '<div class="spinner"></div>'
        : stepState === 'error'
        ? '<span style="color:#FF6B8A">&#10007;</span>'
        : '<span style="color:#555">&#9679;</span>';
    el.innerHTML = `
        <div class="step-icon ${stepState}">${iconHtml}</div>
        <span class="step-label">${label}</span>
        <div class="step-meta" id="${tab}-step-meta-${step}"></div>
    `;
    container.appendChild(el);
    tl.stepElements[step] = el;
    tl.stepOrder.push(step);
}

function updateStepState(tab, step, label, stepState) {
    const tl = tabTimeline[tab];
    const el = tl.stepElements[step];
    if (!el) return;
    const icon = el.querySelector('.step-icon');
    const labelEl = el.querySelector('.step-label');
    labelEl.textContent = label;
    icon.className = `step-icon ${stepState}`;
    if (stepState === 'running') icon.innerHTML = '<div class="spinner"></div>';
    else if (stepState === 'complete') icon.innerHTML = '<span style="color:#5ED68A">&#10003;</span>';
    else if (stepState === 'error') icon.innerHTML = '<span style="color:#FF6B8A">&#10007;</span>';
}

function updateStepProgress(tab, step, progress, total, label) {
    const tl = tabTimeline[tab];
    const el = tl.stepElements[step];
    if (!el) return;
    el.querySelector('.step-label').textContent = label;
    let progEl = el.querySelector('.step-progress');
    if (!progEl) {
        const meta = el.querySelector('.step-meta');
        progEl = document.createElement('div');
        progEl.className = 'step-progress';
        progEl.innerHTML = '<div class="step-progress-bar"></div>';
        meta.prepend(progEl);
    }
    progEl.querySelector('.step-progress-bar').style.width = `${(progress / total * 100).toFixed(0)}%`;
}

function completeStep(tab, step, label, durationMs, tokens) {
    updateStepState(tab, step, label, 'complete');
    const meta = document.getElementById(`${tab}-step-meta-${step}`);
    if (!meta) return;
    const progEl = meta.querySelector('.step-progress');
    if (progEl) progEl.remove();
    let html = '';
    if (durationMs !== undefined) html += `<span class="duration">${(durationMs / 1000).toFixed(1)}s</span>`;
    if (tokens !== undefined && tokens !== null) html += `<span class="tokens">${tokens.toLocaleString()} tokens</span>`;
    meta.innerHTML = html;
}

// ============================================================
// Graph Rendering (shared)
// ============================================================

// LLM failure reason → (title, help copy) shared by live SSE + cached
// replay paths. Keys must match the `reason` strings
// demo_compare.py:_classify_llm_error emits on agentic pipeline errors.

// --- compare.html lines 7559-7595: runPanel ---
function runPanel(pipeline) {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    if (!ticker) { document.getElementById('doc-id-input').focus(); return; }
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const model = document.getElementById('model-select').value;
    const chunkSize = document.getElementById('chunk-size-select') ? document.getElementById('chunk-size-select').value : '12';
    const bundle = document.getElementById('bundle-select').value;

    // Map pipeline to refresh endpoint
    const endpointMap = {
        'kgenskills': `/api/refresh-discovery/${ticker}`,
        'modular': `/api/refresh-agentic-analyst/${ticker}`,
        'gemini': `/api/refresh-agentic-flash/${ticker}`,
    };
    const endpoint = endpointMap[pipeline];
    if (!endpoint) return;

    const params = new URLSearchParams({corpus_kb: corpusKb, model: model, chunk_size: chunkSize, bundle: bundle});
    const btn = event.target.closest('.panel-run-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Running...'; }

    fetch(`${endpoint}?${params}`, {method: 'POST'})
        .then(r => r.json())
        .then(data => {
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9654; Run'; }
            if (data.vis) {
                renderGraph(pipeline, data.vis, data.stats || {});
            }
            // Refresh scores after extraction
            refreshScores();
        })
        .catch(err => {
            console.error('Run panel error:', err);
            if (btn) { btn.disabled = false; btn.innerHTML = '&#9654; Run'; }
        });
}


