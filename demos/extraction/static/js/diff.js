// diff.js — Trained vs. Heuristic Diff panel.
//
// Auto-shows under #compare-content when a fan_out slot and a
// fan_out_trained slot have both completed against the same bundle.
// Aggregation is purely client-side from each slot's cached
// kg.entities[]; no new API. Set diff is per-type with a casefold +
// whitespace-normalize on the entity surface text — cross-type
// collisions ("Apple" as COMPANY vs "Apple" as PRODUCT) are NOT folded
// into the same bucket because that would mis-state agreement.

function _normalizeSurface(s) {
    return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function _entitiesByType(entities) {
    const byType = {};
    (entities || []).forEach(e => {
        const t = e.type || e.entity_type || 'UNKNOWN';
        const surface = e.surface || e.name || e.text || e.surface_form || '';
        const norm = _normalizeSurface(surface);
        if (!norm) return;
        if (!byType[t]) byType[t] = new Map();
        // Map keyed by normalized surface; value is the first-seen original text.
        if (!byType[t].has(norm)) byType[t].set(norm, surface);
    });
    return byType;
}

// Compute the trained-vs-heuristic diff between two slot states.
// Returns { by_type, only_in_a, only_in_b, agreed, total_a, total_b }.
function computeTrainedDiff(slotStateA, slotStateB) {
    const entsA = (slotStateA && slotStateA.kg && slotStateA.kg.entities) || [];
    const entsB = (slotStateB && slotStateB.kg && slotStateB.kg.entities) || [];

    const aByType = _entitiesByType(entsA);
    const bByType = _entitiesByType(entsB);

    const allTypes = new Set([
        ...Object.keys(aByType),
        ...Object.keys(bByType),
    ]);

    const by_type = {};
    const only_in_a = [];
    const only_in_b = [];
    const agreed = [];

    allTypes.forEach(t => {
        const aMap = aByType[t] || new Map();
        const bMap = bByType[t] || new Map();
        const a_count = aMap.size;
        const b_count = bMap.size;
        by_type[t] = { a_count, b_count, delta: b_count - a_count };

        aMap.forEach((surface, norm) => {
            if (bMap.has(norm)) {
                agreed.push({ type: t, surface });
            } else {
                only_in_a.push({ type: t, surface });
            }
        });
        bMap.forEach((surface, norm) => {
            if (!aMap.has(norm)) {
                only_in_b.push({ type: t, surface });
            }
        });
    });

    return {
        by_type,
        only_in_a,
        only_in_b,
        agreed,
        total_a: only_in_a.length + agreed.length,
        total_b: only_in_b.length + agreed.length,
    };
}

function _escape(s) {
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;',
        '"': '&quot;', "'": '&#39;',
    }[c]));
}

function _renderEntityList(items, max) {
    if (!items || items.length === 0) return '<span style="color:#888;">(none)</span>';
    const cap = max == null ? 12 : max;
    const shown = items.slice(0, cap).map(it =>
        `<span style="display:inline-block; padding:2px 6px; margin:2px; background:#1a1a2e; border-radius:3px; font-size:11px;">`
        + `<span style="color:#5B9FE6;">${_escape(it.type)}</span> `
        + `${_escape(it.surface)}</span>`
    ).join('');
    const more = items.length > cap
        ? `<span style="color:#888; font-size:11px; margin-left:6px;">+${items.length - cap} more</span>`
        : '';
    return shown + more;
}

// Find the slot indices for a given strategy (e.g. "fan_out").
// Returns [] if no slot is currently configured to that strategy.
function _slotIndicesForStrategy(strategy) {
    const out = [];
    if (typeof slotState === 'undefined' || !slotState) return out;
    for (let i = 0; i < slotState.length; i++) {
        const s = slotState[i];
        if (s && s.strategy === strategy && s.kg && s.kg.entities) {
            out.push(i);
        }
    }
    return out;
}

// Hook the slot lifecycle: call this on every slot completion + on cached
// loads. Renders the panel only when both a fan_out slot and a
// fan_out_trained slot have a completed graph.
function maybeRenderTrainedDiff() {
    const panel = document.getElementById('trained-diff-panel');
    if (!panel) return;

    const heuristicSlots = _slotIndicesForStrategy('fan_out');
    const trainedSlots = _slotIndicesForStrategy('fan_out_trained');

    if (heuristicSlots.length === 0 || trainedSlots.length === 0) {
        panel.style.display = 'none';
        return;
    }

    const slotIdxA = heuristicSlots[0];
    const slotIdxB = trainedSlots[0];
    const a = slotState[slotIdxA];
    const b = slotState[slotIdxB];

    const diff = computeTrainedDiff(a, b);

    // Context line: bundle pair + slot indices.
    const ctxEl = document.getElementById('trained-diff-context');
    if (ctxEl) {
        const bundleA = a.bundle || '—';
        const bundleB = b.bundle || '—';
        const bundleLine = bundleA === bundleB
            ? `bundle <code>${_escape(bundleA)}</code>`
            : `<code>${_escape(bundleA)}</code> vs. <code>${_escape(bundleB)}</code>`;
        ctxEl.innerHTML = `slot-${slotIdxA} (fan_out) vs. slot-${slotIdxB} (fan_out_trained) · ${bundleLine}`
            + ` · totals: <strong>${diff.total_a}</strong> heuristic / <strong>${diff.total_b}</strong> trained`;
    }

    // Per-type counts.
    const countsEl = document.getElementById('trained-diff-counts');
    if (countsEl) {
        const types = Object.keys(diff.by_type).sort();
        if (types.length === 0) {
            countsEl.innerHTML = '<div style="color:#888;">No entities extracted by either slot.</div>';
        } else {
            const rows = types.map(t => {
                const r = diff.by_type[t];
                const sign = r.delta > 0 ? '+' : (r.delta < 0 ? '' : '±');
                const color = r.delta > 0 ? '#5ED68A' : (r.delta < 0 ? '#E74C3C' : '#888');
                return `<tr>
                    <td style="padding:2px 8px;"><strong>${_escape(t)}</strong></td>
                    <td style="padding:2px 8px; color:#aaa; text-align:right;">fan_out=${r.a_count}</td>
                    <td style="padding:2px 8px; color:#aaa; text-align:right;">fan_out_trained=${r.b_count}</td>
                    <td style="padding:2px 8px; color:${color}; text-align:right; font-weight:600;">Δ ${sign}${r.delta}</td>
                </tr>`;
            }).join('');
            countsEl.innerHTML = `<table style="border-collapse:collapse; font-size:12px;">
                <thead><tr style="color:#888; font-size:11px;"><th style="text-align:left; padding:2px 8px;">Type</th><th style="text-align:right; padding:2px 8px;">Heuristic</th><th style="text-align:right; padding:2px 8px;">Trained</th><th style="text-align:right; padding:2px 8px;">Δ</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
        }
    }

    // Set diff blocks.
    const setsEl = document.getElementById('trained-diff-sets');
    if (setsEl) {
        setsEl.innerHTML = `
            <div style="margin-bottom:8px;"><strong style="color:#E74C3C;">Only in fan_out</strong>
                <span style="color:#888; font-size:11px;"> (${diff.only_in_a.length})</span><br>${_renderEntityList(diff.only_in_a)}</div>
            <div style="margin-bottom:8px;"><strong style="color:#5ED68A;">Only in fan_out_trained</strong>
                <span style="color:#888; font-size:11px;"> (${diff.only_in_b.length})</span><br>${_renderEntityList(diff.only_in_b)}</div>
            <div><strong style="color:#5B9FE6;">Agreed</strong>
                <span style="color:#888; font-size:11px;"> (${diff.agreed.length})</span><br>${_renderEntityList(diff.agreed)}</div>`;
    }

    panel.style.display = '';
}

// Make the API available globally — Node tests pull it from module.exports;
// browser code finds it on window via the script-tag include in compare.html.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { computeTrainedDiff, maybeRenderTrainedDiff };
}
