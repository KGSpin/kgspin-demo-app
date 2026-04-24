// intel-graph-delta.js — Wave J / PRD-056 v2 MH #5
//
// Handles incremental `graph_delta` SSE events emitted by the Intelligence
// pipeline. Each delta represents one article's contribution to the merged
// KG: added entities, added relationships, bridges created, and a topology
// health snapshot.
//
// The final `kg_ready` event is still authoritative for the graph render;
// this module's job is to (a) buffer deltas so commits 4/5 can animate the
// graph + sparkline, (b) drive visible per-article activity in the existing
// Sources list, and (c) surface Pause/Step/Resume controls for the demo
// narration.

(function () {
    'use strict';

    // Public state — commit 5's scrubber + sparkline read from these globals.
    window.__intelDeltaLog = [];          // append-only; survives a run
    window.__intelDeltaQueue = [];        // deltas waiting for Step/Resume
    window.__intelDeltaPaused = false;
    window.__intelDeltaListeners = [];    // external subscribers (commit 5)

    function notifyListeners(delta) {
        for (const fn of window.__intelDeltaListeners) {
            try { fn(delta); } catch (e) { console.error('delta listener error', e); }
        }
    }

    function applyDelta(delta) {
        window.__intelDeltaLog.push(delta);
        // Wave J commit 5: article_index 0 = SEC filing (anchored at t=0),
        // 1+i = news article i. Matches the `article_extracted` convention.
        const article = document.getElementById(`intel-article-${delta.article_index}`);
        if (article) {
            const statusEl = article.querySelector('.intel-article-status');
            if (statusEl) {
                const addedE = (delta.added_entities || []).length;
                const addedR = (delta.added_relationships || []).length;
                const bridges = (delta.bridges_created || []).length;
                const bridgeHtml = bridges > 0
                    ? ` <span class="intel-bridge-badge" title="Cross-hub bridges created">&#x21CC; ${bridges}</span>`
                    : '';
                statusEl.innerHTML = `&#x2713; +${addedE} ents, +${addedR} rels${bridgeHtml}`;
            }
            // Short emphasis pulse so viewers see the delta land.
            article.classList.add('intel-delta-pulse');
            setTimeout(() => article.classList.remove('intel-delta-pulse'), 800);
        }
        updateDeltaCounts();
        notifyListeners(delta);
    }

    function drainQueue() {
        while (!window.__intelDeltaPaused && window.__intelDeltaQueue.length > 0) {
            applyDelta(window.__intelDeltaQueue.shift());
        }
    }

    function onGraphDelta(e) {
        let d;
        try { d = JSON.parse(e.data); } catch (err) { return; }
        if (!d || typeof d !== 'object') return;

        if (window.__intelDeltaPaused) {
            window.__intelDeltaQueue.push(d);
            updateDeltaCounts();
        } else {
            applyDelta(d);
        }
    }

    function updateDeltaCounts() {
        const badge = document.getElementById('intel-delta-queue-badge');
        if (badge) {
            const q = window.__intelDeltaQueue.length;
            badge.textContent = q > 0 ? String(q) : '';
            badge.style.display = q > 0 ? 'inline' : 'none';
        }
    }

    function setPaused(paused) {
        window.__intelDeltaPaused = paused;
        const pauseBtn = document.getElementById('intel-delta-pause');
        const resumeBtn = document.getElementById('intel-delta-resume');
        const stepBtn = document.getElementById('intel-delta-step');
        if (pauseBtn) pauseBtn.disabled = paused;
        if (resumeBtn) resumeBtn.disabled = !paused;
        if (stepBtn) stepBtn.disabled = !paused || window.__intelDeltaQueue.length === 0;
        if (!paused) drainQueue();
    }

    function stepOnce() {
        if (!window.__intelDeltaPaused) return;
        if (window.__intelDeltaQueue.length === 0) return;
        applyDelta(window.__intelDeltaQueue.shift());
        const stepBtn = document.getElementById('intel-delta-step');
        if (stepBtn) stepBtn.disabled = window.__intelDeltaQueue.length === 0;
    }

    function resetDeltaState() {
        window.__intelDeltaLog = [];
        window.__intelDeltaQueue = [];
        window.__intelDeltaPaused = false;
        updateDeltaCounts();
        const pauseBtn = document.getElementById('intel-delta-pause');
        const resumeBtn = document.getElementById('intel-delta-resume');
        if (pauseBtn) pauseBtn.disabled = false;
        if (resumeBtn) resumeBtn.disabled = true;
        const stepBtn = document.getElementById('intel-delta-step');
        if (stepBtn) stepBtn.disabled = true;
    }

    // --- Public API ---

    // `wireIntelDeltaHandler(eventSource)` is called from intelligence.js
    // right after the EventSource is created, so we attach the listener in
    // the same wiring pass as the rest of the intel SSE surface.
    window.wireIntelDeltaHandler = function wireIntelDeltaHandler(eventSource) {
        if (!eventSource) return;
        eventSource.addEventListener('graph_delta', onGraphDelta);
    };

    window.resetIntelDeltaState = resetDeltaState;

    // Register Wave E action names (used by the data-action delegation).
    if (typeof registerAction === 'function') {
        registerAction('intel-delta-pause', () => setPaused(true));
        registerAction('intel-delta-resume', () => setPaused(false));
        registerAction('intel-delta-step', () => stepOnce());
    }
})();
