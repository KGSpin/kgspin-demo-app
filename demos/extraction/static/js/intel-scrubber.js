// intel-scrubber.js — Wave J / PRD-056 v2 MH #6
//
// Horizontal range input below the Intelligence graph. The scrubber
// "replays" graph_delta events up to its current position by hiding any
// vis-network node/edge that wasn't introduced at or before that article.
//
// Core design decision: the scrubber does NOT rebuild the DataSet per
// step. It tags every node/edge on first render with its earliest
// `article_index` (read from the node's / edge's source_refs), then
// scrubbing just flips the `hidden` flag on the existing DataSet —
// fast even at 5-article / 100-node targets.
//
// Filings anchored at article_index=0; news articles have 1+i. When
// `fetched_at` is populated we sort ascending on that field; otherwise
// we fall back to the server-provided article_index (arrival order).

(function () {
    'use strict';

    const PIPELINE = 'intelligence';
    // nodeEarliestIdx[nodeId] = smallest article_index at which the node
    // first appeared in a delta. Same idea for edgeEarliestIdx.
    let nodeEarliestIdx = new Map();
    let edgeEarliestIdx = new Map();
    let maxIndex = 0;
    let articleOrder = [];  // [{article_index, fetched_at, outlet, article_id}]

    function reset() {
        nodeEarliestIdx = new Map();
        edgeEarliestIdx = new Map();
        maxIndex = 0;
        articleOrder = [];
        const input = document.getElementById('intel-scrubber');
        const label = document.getElementById('intel-scrubber-label');
        const container = document.getElementById('intel-scrubber-container');
        if (container) container.style.display = 'none';
        if (input) { input.value = '0'; input.max = '0'; }
        if (label) label.textContent = '';
    }

    // Tag DataSet rows with their earliest article_index. Called after
    // kg_ready lands and after each graph_delta (we index via source_refs
    // which carry the SourceRef list from the backend).
    function indexFromDataSets() {
        const nodes = nodeDataSets[PIPELINE];
        const edges = edgeDataSets[PIPELINE];
        if (!nodes || !edges) return;

        // Article index lookup: article_id → article_index. Populated
        // from the deltaLog (which knows each article_id's article_index).
        const idToIdx = new Map();
        for (const delta of (window.__intelDeltaLog || [])) {
            if (delta.article_id) idToIdx.set(delta.article_id, delta.article_index);
        }

        function earliestArticleIdx(sourceRefs) {
            if (!Array.isArray(sourceRefs) || sourceRefs.length === 0) return 0;
            let best = Infinity;
            for (const s of sourceRefs) {
                if (!s) continue;
                if (s.kind === 'filing') return 0;
                const idx = idToIdx.get(s.article_id);
                if (typeof idx === 'number' && idx < best) best = idx;
            }
            return best === Infinity ? 0 : best;
        }

        nodes.forEach(n => {
            const m = n.metadata || {};
            nodeEarliestIdx.set(n.id, earliestArticleIdx(m.source_refs));
        });
        edges.forEach(e => {
            const m = e.metadata || {};
            edgeEarliestIdx.set(e.id, earliestArticleIdx(m.source_refs));
        });
        maxIndex = Math.max(
            0, ...nodeEarliestIdx.values(), ...edgeEarliestIdx.values(),
        );
    }

    function applyPosition(pos) {
        const nodes = nodeDataSets[PIPELINE];
        const edges = edgeDataSets[PIPELINE];
        if (!nodes || !edges) return;

        edges.update(edges.map(e => ({
            ...e, hidden: (edgeEarliestIdx.get(e.id) || 0) > pos,
        })));
        // A node is visible if (a) it first appeared at or before pos, OR
        // (b) at least one of its adjacent visible edges needs it.
        const keep = new Set();
        edges.forEach(e => {
            if (!e.hidden) { keep.add(e.from); keep.add(e.to); }
        });
        nodes.update(nodes.map(n => {
            const earliest = nodeEarliestIdx.get(n.id) || 0;
            const ownOK = earliest <= pos;
            return { ...n, hidden: !(ownOK || keep.has(n.id)) };
        }));

        const label = document.getElementById('intel-scrubber-label');
        if (label) {
            if (pos === 0) {
                label.textContent = 'Filing baseline (t = 0)';
            } else if (pos >= maxIndex) {
                label.textContent = `All articles (t = ${pos}/${maxIndex})`;
            } else {
                const d = (window.__intelDeltaLog || []).find(x => x.article_index === pos);
                const outlet = d && d.outlet ? ` — ${d.outlet}` : '';
                label.textContent = `t = ${pos}/${maxIndex}${outlet}`;
            }
        }
    }

    function onScrub(e) {
        const pos = parseInt(e.target.value, 10) || 0;
        applyPosition(pos);
    }

    // Called from intelligence.js after kg_ready renders. Indexes the
    // DataSets and configures the slider range.
    window.wireIntelScrubber = function () {
        indexFromDataSets();
        const container = document.getElementById('intel-scrubber-container');
        const input = document.getElementById('intel-scrubber');
        const label = document.getElementById('intel-scrubber-label');
        if (!container || !input) return;
        if (maxIndex <= 0) {
            container.style.display = 'none';
            return;
        }
        container.style.display = '';
        input.max = String(maxIndex);
        input.value = String(maxIndex);  // start at end of playback
        input.removeEventListener('input', onScrub);
        input.addEventListener('input', onScrub);
        if (label) label.textContent = `All articles (t = ${maxIndex}/${maxIndex})`;
    };

    window.resetIntelScrubber = reset;
})();
