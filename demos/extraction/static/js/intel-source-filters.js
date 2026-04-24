// intel-source-filters.js — Wave J / PRD-056 v2 MH #4
//
// Per-source filter checkboxes for the Intelligence graph. Reads the
// `source_origins` field that `build_vis_data` now attaches to every
// node/edge metadata bag, renders one checkbox per origin, and hides
// nodes/edges whose source set is exclusively an unchecked origin.
//
// Client-side only — no SSE round-trip. Toggling a filter rewrites the
// `hidden` flag on existing DataSet rows via DataSet.update().

(function () {
    'use strict';

    const PIPELINE = 'intelligence';
    // Active filter state. When all origins are checked (or the UI has not
    // been rendered yet), we treat every node/edge as visible regardless of
    // its source_origins list.
    let activeOrigins = null;  // Set | null
    let knownOrigins = [];

    function collectOrigins() {
        const origins = new Set();
        const nodes = nodeDataSets[PIPELINE];
        const edges = edgeDataSets[PIPELINE];
        if (nodes) {
            nodes.forEach(n => {
                const m = n.metadata || {};
                for (const o of (m.source_origins || [])) origins.add(o);
            });
        }
        if (edges) {
            edges.forEach(e => {
                const m = e.metadata || {};
                for (const o of (m.source_origins || [])) origins.add(o);
            });
        }
        return Array.from(origins).sort();
    }

    function renderFilters() {
        const container = document.getElementById('intel-source-filters');
        if (!container) return;
        knownOrigins = collectOrigins();
        if (knownOrigins.length === 0) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }
        container.style.display = '';
        container.innerHTML = '<span class="intel-source-filters-label">Source filter:</span>';
        for (const origin of knownOrigins) {
            const id = `intel-source-filter-${origin.replace(/[^a-z0-9]/gi, '_')}`;
            const wrap = document.createElement('label');
            wrap.className = 'intel-source-filter-checkbox';
            wrap.innerHTML = `
                <input type="checkbox" id="${id}" data-origin="${origin}" checked>
                <span>${origin}</span>
            `;
            container.appendChild(wrap);
            wrap.querySelector('input').addEventListener('change', onToggle);
        }
    }

    function onToggle() {
        const container = document.getElementById('intel-source-filters');
        if (!container) return;
        const checked = Array.from(
            container.querySelectorAll('input[type="checkbox"]:checked')
        ).map(el => el.dataset.origin);
        activeOrigins = new Set(checked);
        applyFilter();
    }

    function itemVisibleForOrigins(origins) {
        if (activeOrigins === null) return true;
        if (!origins || origins.length === 0) return true;  // unknown provenance → keep visible
        // Item visible if at least one of its origins is checked.
        for (const o of origins) {
            if (activeOrigins.has(o)) return true;
        }
        return false;
    }

    function applyFilter() {
        const nodes = nodeDataSets[PIPELINE];
        const edges = edgeDataSets[PIPELINE];
        if (!nodes || !edges) return;

        // Determine which edges stay visible based on their own source_origins.
        const visibleEdgeIds = new Set();
        edges.forEach(e => {
            const m = e.metadata || {};
            if (itemVisibleForOrigins(m.source_origins)) {
                visibleEdgeIds.add(e.id);
            }
        });
        edges.update(edges.map(e => ({
            ...e,
            hidden: !visibleEdgeIds.has(e.id),
        })));

        // A node stays visible if (a) it passes its own source filter, OR
        // (b) at least one of its adjacent visible edges keeps it on stage.
        const endpointIds = new Set();
        edges.forEach(e => {
            if (visibleEdgeIds.has(e.id)) {
                endpointIds.add(e.from);
                endpointIds.add(e.to);
            }
        });
        nodes.update(nodes.map(n => {
            const m = n.metadata || {};
            const ownOK = itemVisibleForOrigins(m.source_origins);
            const hasAdjacent = endpointIds.has(n.id);
            // Preserve the existing `hidden` flag for disconnected nodes
            // (Sprint 33.15 WI-3) when the disconnected toggle has hidden
            // them — only *add* hidden state from the filter, don't reveal.
            const filterHidden = !(ownOK || hasAdjacent);
            return { ...n, hidden: filterHidden };
        }));
    }

    function resetFilters() {
        activeOrigins = null;
        const container = document.getElementById('intel-source-filters');
        if (container) {
            container.innerHTML = '';
            container.style.display = 'none';
        }
    }

    // Called after `renderGraph('intelligence', ...)` settles — the vis
    // DataSets are populated by then, so metadata scans work.
    window.renderIntelSourceFilters = function () {
        renderFilters();
        applyFilter();  // first pass is a no-op (all checked) but resets hidden
    };

    window.resetIntelSourceFilters = resetFilters;
})();
