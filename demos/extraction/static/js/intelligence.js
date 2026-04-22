// intelligence.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 4804-4811: intelRefresh ---
function intelRefresh() {
    if (!intelRunState.docId) return;
    // Re-run the full intelligence pipeline
    startIntelligence();
}

// ============================================================
// Run Active Tab (Sprint 33.15b — Bug 1)

// --- compare.html lines 6553-6859: Intelligence (start/render/articles/entities/metadata/source-filter) ---
let _docContextByPipeline = {};  // pipeline -> document_context dict
let _activePopover = null;

function storeDocumentContext(pipeline, docCtx) {
    if (!docCtx) return;
    _docContextByPipeline[pipeline] = docCtx;
    // Show info button for this pipeline
    const btn = document.getElementById(pipeline + '-meta-btn');
    if (btn) btn.style.display = '';
    // Update Document Explorer tab metadata card (use first available context)
    updateIntelMetaCard();
}

function updateIntelMetaCard() {
    const ctx = _docContextByPipeline['kgenskills'] || _docContextByPipeline['modular'] || _docContextByPipeline['gemini'];
    const card = document.getElementById('intel-meta-card');
    const tbody = document.querySelector('#intel-meta-table tbody');
    if (!ctx || !card || !tbody) return;
    tbody.innerHTML = '';
    for (const [key, entry] of Object.entries(ctx)) {
        const val = (typeof entry === 'object' && entry.value !== undefined) ? entry.value : entry;
        const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const row = document.createElement('tr');
        row.innerHTML = `<td>${label}</td><td>${val}</td>`;
        tbody.appendChild(row);
    }
    card.style.display = 'block';
}

function showMetadataPopover(pipeline, btnEl) {
    // Close existing popover
    if (_activePopover) { _activePopover.remove(); _activePopover = null; }
    const ctx = _docContextByPipeline[pipeline];
    if (!ctx) return;
    const pop = document.createElement('div');
    pop.className = 'meta-popover';
    let html = '<h4>Document Metadata</h4><table>';
    for (const [key, entry] of Object.entries(ctx)) {
        const val = (typeof entry === 'object' && entry.value !== undefined) ? entry.value : entry;
        const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        html += `<tr><td>${label}</td><td>${val}</td></tr>`;
    }
    html += '</table>';
    pop.innerHTML = html;
    // Position near button
    const rect = btnEl.getBoundingClientRect();
    pop.style.top = (rect.bottom + 4) + 'px';
    pop.style.left = Math.max(8, rect.left - 100) + 'px';
    document.body.appendChild(pop);
    _activePopover = pop;
    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', function _close(e) {
            if (!pop.contains(e.target) && e.target !== btnEl) {
                pop.remove(); _activePopover = null;
                document.removeEventListener('click', _close);
            }
        });
    }, 10);
}

// ============================================================
// Tab 1: Document Explorer
// ============================================================
function startIntelligence() {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    if (!ticker) {
        document.getElementById('doc-id-input').focus();
        return;
    }
    state.docId = ticker;

    // Reset UI
    document.getElementById('intel-welcome').style.display = 'none';
    document.getElementById('intel-timeline').style.display = 'block';
    document.getElementById('intel-content').style.display = 'block';
    document.getElementById('intel-article-list').innerHTML = '';
    document.getElementById('intel-entity-feed').innerHTML = '';
    document.getElementById('intel-article-count').textContent = '0 articles';
    document.getElementById('intelligence-graph').innerHTML = '<div class="placeholder">Waiting for intelligence pipeline...</div>';
    document.getElementById('intel-timeline-steps').innerHTML = '';
    document.getElementById('intelligence-toolbar').style.display = 'none';
    document.getElementById('intel-history').style.display = 'none';
    document.getElementById('intelligence-legend').innerHTML = '';
    document.getElementById('intelligence-rel-legend').innerHTML = '';
    const tl = tabTimeline.intelligence;
    tl.stepOrder.length = 0;
    for (const k in tl.stepElements) delete tl.stepElements[k];
    delete networks['intelligence'];
    state.intelligence = { kg: null, articles: [], entities: [] };
    // Sprint 33.17 (WI-4): Reset Intel history state
    intelRunState.docId = ticker;
    intelRunState.currentIndex = 0;
    intelRunState.totalRuns = 0;
    activeSourceFilter = null;

    document.getElementById('status').textContent = 'Running intelligence...';
    document.getElementById('intel-run-btn').disabled = true;

    if (eventSource) eventSource.close();
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const gemModel = document.getElementById('model-select').value;
    eventSource = new EventSource(`/api/intelligence/${ticker}?corpus_kb=${corpusKb}&model=${gemModel}`);

    eventSource.addEventListener('step_start', (e) => {
        const d = JSON.parse(e.data);
        addTimelineStep('intelligence', d.step, d.label, 'running');
    });

    eventSource.addEventListener('step_progress', (e) => {
        const d = JSON.parse(e.data);
        updateStepProgress('intelligence', d.step, d.progress, d.total, d.label);
    });

    eventSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('intelligence', d.step, d.label, d.duration_ms, d.tokens);
    });

    eventSource.addEventListener('article_fetched', (e) => {
        const d = JSON.parse(e.data);
        addIntelArticle(d);
    });

    eventSource.addEventListener('news_empty', (e) => {
        // Sprint 06 Task 2 (VP Prod): distinguish missing-key from zero-results.
        const d = JSON.parse(e.data);
        const list = document.getElementById('intel-article-list');
        const emptyMsg = document.createElement('div');
        emptyMsg.className = 'intel-article-empty';
        let hint = '';
        if (d.reason === 'newsapi_configuration') {
            hint = '<a href="https://newsapi.org/register" target="_blank" style="color:#5B9FE6;">Get a free NEWSAPI_KEY at newsapi.org »</a>';
        } else if (d.reason === 'zero_results') {
            hint = 'Try a different ticker or company name.';
        }
        emptyMsg.innerHTML = `
            <div style="color: #888; font-size: 12px; padding: 12px; text-align: center;">
                ${d.message || 'No news articles found'}
                <div style="margin-top: 6px; font-size: 11px; color: #666;">${hint}</div>
            </div>
        `;
        list.appendChild(emptyMsg);
    });

    eventSource.addEventListener('entity_discovered', (e) => {
        const d = JSON.parse(e.data);
        addIntelEntity(d);
    });

    // Sprint 33.16: Per-article extraction progress bar
    eventSource.addEventListener('article_progress', (e) => {
        const d = JSON.parse(e.data);
        const progress = document.getElementById(`intel-article-progress-${d.article_idx}`);
        const bar = document.getElementById(`intel-article-bar-${d.article_idx}`);
        if (progress && bar) {
            progress.classList.add('active');
            bar.style.width = `${Math.round((d.progress / d.total) * 100)}%`;
        }
    });

    // Sprint 33.13: Real per-article extraction progress
    eventSource.addEventListener('article_extracted', (e) => {
        const d = JSON.parse(e.data);
        const item = document.getElementById(`intel-article-${d.article_idx}`);
        if (item) {
            item.classList.add('done');
            const status = item.querySelector('.intel-article-status');
            if (status) status.innerHTML = `&#x2713; ${d.entities || 0} entities, ${d.relationships || 0} rels`;
            const progress = document.getElementById(`intel-article-progress-${d.article_idx}`);
            if (progress) progress.style.display = 'none';
        }
    });

    eventSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        state.intelligence.kg = d;
        renderGraph('intelligence', d.vis, d.stats);
        // Sprint 33.17 (WI-4): Intelligence history bar
        if (typeof d.total_runs === 'number' && d.total_runs > 0) {
            intelRunState.docId = state.docId;
            intelRunState.currentIndex = 0;
            intelRunState.totalRuns = d.total_runs;
            updateIntelRunUI();
            document.getElementById('intel-run-meta').textContent = 'live extraction';
        }
    });

    eventSource.addEventListener('error', (e) => {
        if (e.data) {
            const d = JSON.parse(e.data);
            addTimelineStep('intelligence', d.step || 'error', d.message, 'error');
        } else {
            document.getElementById('status').textContent = 'Connection lost';
            eventSource.close();
        }
        document.getElementById('intel-run-btn').disabled = false;
    });

    eventSource.addEventListener('done', (e) => {
        const d = JSON.parse(e.data);
        document.getElementById('status').textContent = `Complete (${(d.total_duration_ms / 1000).toFixed(1)}s)`;
        document.getElementById('intel-run-btn').disabled = false;
        eventSource.close();
    });
}

function addIntelArticle(d) {
    const list = document.getElementById('intel-article-list');
    const sourceClass = (d.source || '').includes('sec') ? 'sec' : (d.source || '').includes('health') ? 'healthcare' : 'news';
    const sourceLabel = (d.source || '').includes('sec') ? 'SEC' : (d.source || '').includes('health') ? 'FDA' : 'NEWS';
    const idx = state.intelligence.articles.length;
    const item = document.createElement('div');
    item.className = 'intel-article-item';
    item.id = `intel-article-${idx}`;
    item.onclick = () => filterBySource(idx);
    // Sprint 06 Task 2 (VP Prod): render published_at + source_name when available
    const pubDate = d.published_at ? new Date(d.published_at).toLocaleDateString() : '';
    const titleText = d.title || d.source || 'Article';
    const titleHtml = d.url
        ? `<a href="${d.url}" target="_blank" style="color:#c9d1d9; text-decoration:none;" title="Open in newsapi.org">${titleText}</a>`
        : titleText;
    item.innerHTML = `
        <div class="intel-article-title">${titleHtml}</div>
        <div class="intel-article-meta">
            <span class="intel-source-badge ${sourceClass}">${sourceLabel}</span>
            ${d.source_name ? `<span style="color:#7d8590;">${d.source_name}</span>` : ''}
            ${pubDate ? `<span style="color:#6e7681;">${pubDate}</span>` : ''}
            ${d.chars ? `<span>${(d.chars / 1000).toFixed(1)}K chars</span>` : ''}
            <span class="intel-article-status">${d.cached ? '&#x2713; cached' : ''}</span>
        </div>
        <div class="intel-article-progress" id="intel-article-progress-${idx}">
            <div class="intel-article-progress-bar" id="intel-article-bar-${idx}"></div>
        </div>
    `;
    list.appendChild(item);
    // Sprint 33.17 (WI-2): Store source_id for source filtering
    d.source_id = d.source_id || d.source || 'unknown';
    state.intelligence.articles.push(d);
    document.getElementById('intel-article-count').textContent = `${state.intelligence.articles.length} articles`;
    list.scrollTop = list.scrollHeight;
}

function addIntelEntity(d) {
    const feed = document.getElementById('intel-entity-feed');
    const tag = document.createElement('span');
    tag.className = 'entity-feed-tag';
    tag.style.borderColor = TYPE_COLORS[d.type] || '#AAA';
    tag.style.color = TYPE_COLORS[d.type] || '#AAA';
    tag.textContent = d.text;
    feed.appendChild(tag);
    state.intelligence.entities.push(d);
}

// Sprint 33.17 (WI-2): Source filtering — click article to show only its entities/rels
let activeSourceFilter = null;

function filterBySource(articleIdx) {
    const article = state.intelligence.articles[articleIdx];
    if (!article) return;
    const sourceId = article.source_id;

    // Toggle: click same article again to clear filter
    if (activeSourceFilter === articleIdx) {
        activeSourceFilter = null;
        // Remove active class from all articles
        document.querySelectorAll('.intel-article-item.source-active').forEach(el => el.classList.remove('source-active'));
        // Show all nodes/edges (respect disconnected toggle)
        if (nodeDataSets['intelligence'] && edgeDataSets['intelligence']) {
            const showDisc = showDisconnected['intelligence'] || false;
            const connIds = new Set();
            edgeDataSets['intelligence'].forEach(e => { connIds.add(e.from); connIds.add(e.to); });
            edgeDataSets['intelligence'].update(edgeDataSets['intelligence'].map(e => ({ ...e, hidden: false })));
            nodeDataSets['intelligence'].update(nodeDataSets['intelligence'].map(n => ({
                ...n, hidden: !showDisc && !connIds.has(n.id),
            })));
        }
        return;
    }

    // Activate filter
    activeSourceFilter = articleIdx;
    document.querySelectorAll('.intel-article-item.source-active').forEach(el => el.classList.remove('source-active'));
    const el = document.getElementById(`intel-article-${articleIdx}`);
    if (el) el.classList.add('source-active');

    if (!nodeDataSets['intelligence'] || !edgeDataSets['intelligence']) return;

    // Filter edges: show only edges from this source
    const visibleNodeIds = new Set();
    edgeDataSets['intelligence'].update(edgeDataSets['intelligence'].map(e => {
        const match = e.metadata && e.metadata.source_document === sourceId;
        if (match) { visibleNodeIds.add(e.from); visibleNodeIds.add(e.to); }
        return { ...e, hidden: !match };
    }));

    // Filter nodes: show only nodes that have this source in their sources array
    nodeDataSets['intelligence'].update(nodeDataSets['intelligence'].map(n => {
        const hasSrc = n.metadata && n.metadata.sources && n.metadata.sources.includes(sourceId);
        const hasEdge = visibleNodeIds.has(n.id);
        return { ...n, hidden: !(hasSrc || hasEdge) };
    }));
}

// ============================================================
// Tab 3: Graph Impact — Data Lineage
// ============================================================

