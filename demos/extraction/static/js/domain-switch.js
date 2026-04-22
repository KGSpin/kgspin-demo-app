// domain-switch.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 4824-4825: currentDomain ---
let currentDomain = 'financial';


// --- compare.html lines 4930-5005: switchDomain ---
function switchDomain(domain) {
    if (domain === currentDomain) return;
    currentDomain = domain;

    // Update pill styles
    document.querySelectorAll('.domain-pill').forEach(pill => {
        if (pill.dataset.domain === domain) {
            pill.classList.add('active');
            pill.style.background = '#1a2a3a';
            pill.style.color = domain === 'clinical' ? '#d4a5d4' : '#7cb3ff';
        } else {
            pill.classList.remove('active');
            pill.style.background = '#0f0f23';
            pill.style.color = '#888';
        }
    });

    // Swap corpus selector: ticker input vs trial dropdown
    const tickerInput = document.getElementById('doc-id-input');
    const tickerList = document.getElementById('doc-id-list');
    const trialSelect = document.getElementById('trial-select');
    const clinicalBadge = document.getElementById('clinical-badge');

    if (domain === 'clinical') {
        tickerInput.style.display = 'none';
        tickerList.style.display = 'none';
        trialSelect.style.display = '';
        clinicalBadge.style.display = '';
        loadTrials();
    } else {
        tickerInput.style.display = '';
        tickerList.style.display = '';
        trialSelect.style.display = 'none';
        clinicalBadge.style.display = 'none';
    }

    // Reload bundles for new domain — clear slot bundle cache too
    bundleOptions = null;
    loadBundles(domain);

    // Reset all slot dropdowns since bundles changed
    for (let i = 0; i < 3; i++) {
        const pSel = document.getElementById(`slot-${i}-pipeline`);
        if (pSel) pSel.value = '';
        const bSel = document.getElementById(`slot-${i}-bundle`);
        if (bSel) { bSel.innerHTML = '<option value="">— Select Bundle —</option>'; bSel.style.display = 'none'; }
        slotState[i] = { pipeline: '', bundle: '', strategy: '', visData: null, stats: null, kg: null };
        const graph = document.getElementById(`slot-${i}-graph`);
        if (graph) graph.innerHTML = '<div class="placeholder">Select a pipeline above</div>';
        const stats = document.getElementById(`slot-${i}-stats`);
        if (stats) stats.innerHTML = '';
        const toolbar = document.getElementById(`slot-${i}-toolbar`);
        if (toolbar) toolbar.style.display = 'none';
        const runBtn = document.getElementById(`slot-${i}-run`);
        if (runBtn) runBtn.style.display = 'none';
        const sub = document.getElementById(`slot-${i}-subtitle`);
        if (sub) sub.textContent = '';
        if (networks[`slot-${i}`]) { networks[`slot-${i}`].destroy(); delete networks[`slot-${i}`]; }
    }

    // Clear existing graphs (Task 5b: graph refresh on domain change)
    clearAllGraphs();

    // Reset per-pipeline run-state globals so pager arrows / cached lookups
    // don't fire stale `/api/gemini-runs/<prev-doc_id>/<idx>` calls after
    // the domain changed. Without this, switching financial → clinical
    // re-runs the pager against the prior doc_id and surfaces 422s in
    // the console (stale doc_id + NaN index from the in-flight
    // `currentIndex` math).
    [gemRunState, modRunState, kgenRunState, intelRunState].forEach(s => {
        s.docId = null;
        s.currentIndex = 0;
        s.totalRuns = 0;
    });
}


// --- compare.html lines 5006-5016: clearAllGraphs ---
function clearAllGraphs() {
    // Clear KGSpin graph
    const kgGraph = document.getElementById('kgenskills-graph');
    if (kgGraph) kgGraph.innerHTML = '<div class="placeholder">Select a ' + (currentDomain === 'clinical' ? 'trial' : 'ticker') + ' and click Go</div>';
    // Clear LLM graphs
    const fsGraph = document.getElementById('gemini-graph');
    if (fsGraph) fsGraph.innerHTML = '<div class="placeholder">Waiting for extraction...</div>';
    const msGraph = document.getElementById('modular-graph');
    if (msGraph) msGraph.innerHTML = '<div class="placeholder">Waiting for extraction...</div>';
}


