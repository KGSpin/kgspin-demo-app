// intel-sparkline.js — Wave J / PRD-056 v2 MH #7
//
// Renders a small inline-SVG sparkline above the Intelligence graph. One
// data point per `graph_delta` event; y = `delta.health.score`. Subscribes
// to the delta stream via `window.__intelDeltaListeners` so it stays live
// as events arrive.
//
// No new deps — uses plain SVG. Hover points reveal score + delta from
// the previous point + outlet + article_id.

(function () {
    'use strict';

    const WIDTH = 220;
    const HEIGHT = 42;
    const PAD_X = 6;
    const PAD_Y = 4;

    // Series is a parallel array to `window.__intelDeltaLog`. Each entry:
    //   { score, outlet, article_id, article_index, delta_score }
    const series = [];

    function reset() {
        series.length = 0;
        render();
    }

    function pushDelta(delta) {
        const health = delta.health || {};
        let score = typeof health.score === 'number' ? health.score : -1;
        // Clamp the sentinel (-1, meaning "insufficient signal") to 0 for
        // display, but keep the raw value in metadata for hover.
        const display = score < 0 ? 0 : score;
        const prev = series.length > 0 ? series[series.length - 1].display : display;
        series.push({
            score,
            display,
            delta_score: display - prev,
            outlet: delta.outlet || '',
            article_id: delta.article_id || '',
            article_index: delta.article_index,
            insufficient: score < 0 ? (health.insufficient_reason || '') : '',
        });
        render();
    }

    function render() {
        const container = document.getElementById('intel-sparkline');
        if (!container) return;
        if (series.length === 0) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }
        container.style.display = '';

        // Autoscale y to [0, max] with a small headroom so a flat-zero
        // line still renders mid-panel.
        const maxVal = Math.max(...series.map(s => s.display), 1);
        const xStep = series.length > 1
            ? (WIDTH - 2 * PAD_X) / (series.length - 1)
            : 0;

        const points = series.map((s, i) => {
            const x = PAD_X + i * xStep;
            const y = HEIGHT - PAD_Y - ((s.display / maxVal) * (HEIGHT - 2 * PAD_Y));
            return [x, y];
        });

        const pathData = points.map(
            ([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
        ).join(' ');

        const dots = points.map(([x, y], i) => {
            const s = series[i];
            const tooltip = [
                `score: ${s.insufficient ? 'n/a' : s.display.toFixed(2)}`,
                i > 0 ? `Δ ${s.delta_score >= 0 ? '+' : ''}${s.delta_score.toFixed(2)}` : '',
                s.outlet ? `outlet: ${s.outlet}` : '',
                s.article_id ? `id: ${s.article_id}` : '',
            ].filter(Boolean).join(' | ');
            const cls = s.insufficient ? 'intel-sparkline-dot insufficient' : 'intel-sparkline-dot';
            return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" class="${cls}"><title>${tooltip}</title></circle>`;
        }).join('');

        const latest = series[series.length - 1];
        const latestLabel = latest.insufficient
            ? 'Topology: n/a'
            : `Topology ${latest.display.toFixed(2)} (Δ ${latest.delta_score >= 0 ? '+' : ''}${latest.delta_score.toFixed(2)})`;

        container.innerHTML = `
            <div class="intel-sparkline-row">
                <span class="intel-sparkline-label">${latestLabel}</span>
                <svg width="${WIDTH}" height="${HEIGHT}" class="intel-sparkline-svg" aria-label="Topological Health sparkline">
                    <path d="${pathData}" class="intel-sparkline-path" fill="none" stroke="#5B9FE6" stroke-width="1.5"/>
                    ${dots}
                </svg>
            </div>
        `;
    }

    // Register with the shared delta listener list (intel-graph-delta.js).
    if (Array.isArray(window.__intelDeltaListeners)) {
        window.__intelDeltaListeners.push(pushDelta);
    }
    window.resetIntelSparkline = reset;
})();
