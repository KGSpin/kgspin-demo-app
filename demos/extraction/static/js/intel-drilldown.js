// intel-drilldown.js — Wave J / PRD-056 v2 MH #8
//
// Conflict-stack drilldown block for the existing detail panel. When a
// node/edge has multiple SourceRef entries, renders them as a stacked
// list — newest-by-fetched_at first — so reviewers can see which filing
// and which news articles each attribute came from.
//
// Surface: two helpers bolted onto `window` so graph.js can splice them
// into its existing `detail-body` HTML.

(function () {
    'use strict';

    function fmtFetchedAt(s) {
        if (!s) return '';
        // Accept both ISO and empty-string fallback. Shorten the common
        // "2026-04-22T10:00:00Z" shape for the stack row.
        const t = String(s).replace('T', ' ').replace('Z', ' UTC');
        return t.length > 22 ? t.slice(0, 22) + '…' : t;
    }

    function kindBadge(kind) {
        if (!kind) return '';
        const color = kind === 'filing' ? '#d4a017'
                    : kind === 'news_article' ? '#5B9FE6'
                    : '#8a8aa0';
        return `<span class="intel-drilldown-kind-badge" style="color:${color};border-color:${color}44;">${kind}</span>`;
    }

    function sortRefs(refs) {
        const copy = refs.slice();
        copy.sort((a, b) => {
            // Newest fetched_at first; empty strings sink.
            const ax = a.fetched_at || '';
            const bx = b.fetched_at || '';
            if (ax && bx) return bx.localeCompare(ax);
            if (ax && !bx) return -1;
            if (bx && !ax) return 1;
            // Stable-ish fallback: prefer filings at top.
            if (a.kind === 'filing' && b.kind !== 'filing') return -1;
            if (b.kind === 'filing' && a.kind !== 'filing') return 1;
            return 0;
        });
        return copy;
    }

    window.buildSourceStack = function buildSourceStack(meta) {
        const refs = (meta && meta.source_refs) || [];
        if (!Array.isArray(refs) || refs.length === 0) return '';
        const rows = sortRefs(refs).map(r => {
            const articleId = r.article_id || '';
            const origin = r.origin || '(unknown)';
            return `
                <li class="intel-drilldown-row">
                    ${kindBadge(r.kind)}
                    <span class="intel-drilldown-origin">${origin}</span>
                    <span class="intel-drilldown-article" title="${articleId}">${articleId ? articleId.slice(-20) : ''}</span>
                    <span class="intel-drilldown-fetched">${fmtFetchedAt(r.fetched_at)}</span>
                </li>`;
        }).join('');
        const countLabel = refs.length > 1 ? `Sources (${refs.length})` : 'Source';
        return `
            <div class="detail-row">
                <div class="detail-label">${countLabel}</div>
                <ul class="intel-drilldown-stack">${rows}</ul>
            </div>`;
    };

    window.buildBridgeBadge = function buildBridgeBadge(meta) {
        if (!meta || !meta.is_bridge) return '';
        const subj = meta.subject_hub_ref ? meta.subject_hub_ref.canonical_name : '';
        const obj = meta.object_hub_ref ? meta.object_hub_ref.canonical_name : '';
        return `
            <div class="detail-row">
                <div class="detail-label">Cross-hub bridge</div>
                <div class="detail-value" style="color:#E67E5B;font-weight:600;">
                    ${subj} &#x21CC; ${obj}
                </div>
            </div>`;
    };
})();
