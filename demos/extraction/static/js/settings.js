// settings.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 4826-4902: _bundleLookup + loadBundles + updateLinguisticDropdown + updateBundleId ---
// Sprint 86 Addendum: Strategy × Linguistic bundle options
let _bundleLookup = [];  // Array of {bundle_id, strategy, linguistic}

async function loadBundles(domain) {
    try {
        const resp = await fetch(`/api/bundle-options?domain=${domain}`);
        const data = await resp.json();
        _bundleLookup = data.bundles || [];

        // Populate strategy dropdown
        const sSel = document.getElementById('strategy-select');
        sSel.innerHTML = '';
        const strategyLabels = {
            'fan_out': 'Signal Fan-out',
            'discovery_rapid': 'Rapid Discovery',
            'discovery_deep': 'Deep Discovery',
        };
        const strategyTooltips = {
            'fan_out': 'Relation-first, zero-token',
            'discovery_rapid': 'Linguistic baseline, zero-token',
            'discovery_deep': 'Neural-hybrid, zero-token',
        };
        (data.strategies || []).forEach((s, i) => {
            const opt = document.createElement('option');
            opt.value = s;
            opt.textContent = strategyLabels[s] || s;
            opt.title = strategyTooltips[s] || '';
            sSel.appendChild(opt);
        });
        // Pre-select strategy from default bundle
        const defaultBundle = _bundleLookup.find(b => b.bundle_id === data.default_bundle_id);
        if (defaultBundle) sSel.value = defaultBundle.strategy;

        // Populate linguistic dropdown (filtered by selected strategy)
        updateLinguisticDropdown();
        // Set default linguistic
        if (defaultBundle) {
            document.getElementById('linguistic-select').value = defaultBundle.linguistic;
        }
        updateBundleId();
    } catch(e) { console.warn('Failed to load bundle options:', e); }
}

function updateLinguisticDropdown() {
    const strategy = document.getElementById('strategy-select').value;
    const lSel = document.getElementById('linguistic-select');
    const prevValue = lSel.value;
    lSel.innerHTML = '';

    // Cross-filter: only show linguistics available for the selected strategy
    const available = new Set();
    _bundleLookup.forEach(b => {
        if (b.strategy === strategy) available.add(b.linguistic);
    });

    const sorted = Array.from(available).sort().reverse();
    sorted.forEach((l, i) => {
        const opt = document.createElement('option');
        opt.value = l;
        opt.textContent = l;
        lSel.appendChild(opt);
    });
    // Try to keep previous selection
    if (available.has(prevValue)) {
        lSel.value = prevValue;
    }
}

function updateBundleId() {
    updateLinguisticDropdown();
    const strategy = document.getElementById('strategy-select').value;
    const linguistic = document.getElementById('linguistic-select').value;
    // Lookup opaque bundle_id — no string composition (VP Eng mandate)
    const match = _bundleLookup.find(b => b.strategy === strategy && b.linguistic === linguistic);
    document.getElementById('bundle-select').value = match ? match.bundle_id : '';
}


// --- compare.html lines 7537-7558: toggleSettingsPanel + syncModelSetting + syncCorpusSetting ---
function toggleSettingsPanel() {
    const panel = document.getElementById('settings-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}
function syncModelSetting() {
    const val = document.getElementById('settings-model-select').value;
    document.getElementById('model-select').value = val;
}
function syncCorpusSetting() {
    const val = document.getElementById('settings-corpus-select').value;
    document.getElementById('corpus-kb-select').value = val;
}
// Close settings panel when clicking outside
document.addEventListener('click', function(e) {
    const panel = document.getElementById('settings-panel');
    const btn = document.getElementById('settings-btn');
    if (panel && panel.style.display === 'block' && !panel.contains(e.target) && e.target !== btn) {
        panel.style.display = 'none';
    }
});

// --- Per-Graph Run ---

