// compare-runner.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 3317-4040: Auto-flag + flag explorer + stored feedback orchestration ---
async function runSlotAutoFlag(slotIdx) {
    const pipeline = `slot-${slotIdx}`;
    const fpBtn = document.getElementById(`slot-${slotIdx}-discover-fp`);
    const statusEl = document.getElementById(`slot-${slotIdx}-discover-status`);
    await _runAutoFlagForPipeline(pipeline, fpBtn, statusEl, slotIdx);
}

// Legacy entry point from Flags tab button
async function runAutoFlag() {
    // Find first slot with data and run on it
    for (let i = 0; i < 3; i++) {
        const pipeline = `slot-${i}`;
        if (nodeDataSets[pipeline] && edgeDataSets[pipeline]) {
            const statusEl = document.getElementById('auto-flag-status');
            const btn = document.getElementById('auto-flag-btn');
            await _runAutoFlagForPipeline(pipeline, btn, statusEl, i);
            return;
        }
    }
    showToast('Run extraction first', 'error');
}

async function _runAutoFlagForPipeline(pipeline, btn, statusEl, slotIdx) {
    const nodes = nodeDataSets[pipeline];
    const edges = edgeDataSets[pipeline];
    if (!nodes || !edges) {
        showToast('Run extraction in this slot first', 'error');
        return;
    }
    if (btn) btn.disabled = true;
    if (statusEl) {
        statusEl.textContent = 'AI analyzing FPs...';
        statusEl.style.color = '#d4a017';
    }

    // Resolve bundle name from slot state
    const slotBundleSel = document.getElementById(`slot-${slotIdx}-bundle`);
    const bundleName = slotBundleSel ? slotBundleSel.value : null;

    try {
        const nodeItems = nodes.get().filter(n => n.metadata);
        const edgeItems = edges.get().filter(e => e.metadata);

        const resp = await fetch('/api/feedback/auto_flag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker: state.docId || '',
                bundle_name: bundleName || null,
                nodes: nodeItems.map(n => ({ id: n.id, ...n.metadata })),
                edges: edgeItems.map(e => ({ id: e.id, ...e.metadata })),
            }),
        });
        const data = await resp.json();
        if (resp.status !== 200) {
            showToast(data.error || 'Auto-flag failed', 'error');
            if (statusEl) { statusEl.textContent = 'Failed'; statusEl.style.color = '#ff6b6b'; }
            return;
        }
        const flags = data.flags || [];
        if (data.bundle_entity_types) window._bundleEntityTypes = data.bundle_entity_types;
        if (data.bundle_type_hierarchy) window._bundleTypeHierarchy = data.bundle_type_hierarchy;
        if (data.bundle_type_definitions) window._bundleTypeDefinitions = data.bundle_type_definitions;
        // VP Eng guardrail G1: auto-flags are ephemeral (NOT stored to FeedbackStore)
        for (const flag of flags) {
            if (flag.type === 'node') {
                const key = `auto_entity_${pipeline}_${flag.id}`;
                const nodeMeta = (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][flag.id]) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][String(flag.id)]);
                const nodeLabel = nodeMeta ? nodeMeta.text : `Node ${flag.id}`;
                const nodeEntityType = nodeMeta ? nodeMeta.entity_type : '';
                feedbackState[key] = { type: 'auto_entity_fp', reasons: flag.reasons || [], reason_detail: flag.reason_detail || '', corrected_type: flag.corrected_type || '', resolve_to_entity: flag.resolve_to_entity || '', confirmed: false, label: nodeLabel, entity_type: nodeEntityType, evidence_sentence: flag.evidence_sentence || '' };
                nodes.update({ id: flag.id, color: { background: '#FF8C00', border: '#FF6B00', highlight: { background: '#FF8C00', border: '#FF6B00' } } });
            } else if (flag.type === 'edge') {
                const key = `auto_${pipeline}_${flag.id}`;
                const edgeMeta = (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][flag.id]);
                const edgeLabel = edgeMeta ? `${edgeMeta.subject_text} → ${edgeMeta.predicate} → ${edgeMeta.object_text}` : `Edge ${flag.id}`;
                feedbackState[key] = { type: 'auto_edge_fp', reasons: flag.reasons || [], reason_detail: flag.reason_detail || '', confirmed: false, label: edgeLabel };
                edges.update({ id: flag.id, color: { color: '#FF8C00', highlight: '#FF8C00' } });
            }
        }
        if (statusEl) {
            statusEl.textContent = `${flags.length} FP${flags.length !== 1 ? 's' : ''}`;
            statusEl.style.color = flags.length > 0 ? '#FF8C00' : '#4DD4C0';
        }
        showToast(`FP discovery: ${flags.length} issue${flags.length !== 1 ? 's' : ''} detected`, 'info');
        try { renderFlagExplorer(); } catch (renderErr) { console.error('renderFlagExplorer error:', renderErr); }
        // Auto-switch to Flags tab so user sees the results
        if (flags.length > 0) switchTab('flags');
    } catch (e) {
        console.error('Auto-flag error:', e);
        showToast('FP discovery failed', 'error');
        if (statusEl) { statusEl.textContent = 'Error'; statusEl.style.color = '#ff6b6b'; }
    } finally {
        if (btn) btn.disabled = false;
    }
}

// Sprint 120: Missing TP discovery — LLM compares graph against source document
async function runSlotDiscoverTP(slotIdx) {
    const pipeline = `slot-${slotIdx}`;
    const nodes = nodeDataSets[pipeline];
    const edges = edgeDataSets[pipeline];
    if (!nodes || !edges) {
        showToast('Run extraction in this slot first', 'error');
        return;
    }
    const tpBtn = document.getElementById(`slot-${slotIdx}-discover-tp`);
    const statusEl = document.getElementById(`slot-${slotIdx}-discover-status`);
    if (tpBtn) tpBtn.disabled = true;
    if (statusEl) {
        statusEl.textContent = 'AI selecting gold data...';
        statusEl.style.color = '#d4a017';
    }

    const slotBundleSel = document.getElementById(`slot-${slotIdx}-bundle`);
    const bundleName = slotBundleSel ? slotBundleSel.value : null;

    try {
        const nodeItems = nodes.get().filter(n => n.metadata);
        const edgeItems = edges.get().filter(e => e.metadata);

        const resp = await fetch('/api/feedback/auto_discover_tp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker: state.docId || '',
                bundle_name: bundleName || null,
                nodes: nodeItems.map(n => ({ id: n.id, ...n.metadata })),
                edges: edgeItems.map(e => ({ id: e.id, ...e.metadata })),
            }),
        });
        const data = await resp.json();
        if (resp.status !== 200) {
            showToast(data.error || 'TP discovery failed', 'error');
            if (statusEl) { statusEl.textContent = 'Failed'; statusEl.style.color = '#ff6b6b'; }
            return;
        }
        const discoveries = data.discoveries || [];
        console.log('TP discoveries received:', discoveries.length, discoveries);
        // Store as auto-flagged TPs (ephemeral, pending user confirmation)
        for (let di = 0; di < discoveries.length; di++) {
            const d = discoveries[di];
            const key = `auto_tp_${pipeline}_${di}`;
            if (d.discovery_type === 'entity') {
                feedbackState[key] = {
                    type: 'auto_tp',
                    discovery_type: 'entity',
                    node_id: d.node_id,
                    entity_text: d.entity_text || '',
                    entity_type: d.entity_type || '',
                    confidence: d.confidence || 0,
                    reason_detail: d.reason_detail || '',
                    confirmed: false,
                    label: `${d.entity_text} (${d.entity_type})`,
                    pipeline: pipeline,
                };
            } else {
                feedbackState[key] = {
                    type: 'auto_tp',
                    discovery_type: 'relationship',
                    edge_id: d.edge_id,
                    subject_text: d.subject_text || '',
                    predicate: d.predicate || '',
                    object_text: d.object_text || '',
                    confidence: d.confidence || 0,
                    reason_detail: d.reason_detail || '',
                    confirmed: false,
                    label: `${d.subject_text} → ${d.predicate} → ${d.object_text}`,
                    pipeline: pipeline,
                };
            }
        }
        if (statusEl) {
            const prev = statusEl.textContent;
            const fpPart = prev.includes('FP') ? prev.split('|')[0].trim() + ' | ' : '';
            statusEl.textContent = `${fpPart}${discoveries.length} TP${discoveries.length !== 1 ? 's' : ''}`;
            statusEl.style.color = discoveries.length > 0 ? '#d4a017' : '#4DD4C0';
        }
        showToast(`Gold data: ${discoveries.length} high-quality extraction${discoveries.length !== 1 ? 's' : ''} selected`, 'info');
        try { renderFlagExplorer(); } catch (renderErr) { console.error('renderFlagExplorer error:', renderErr); }
        // Auto-switch to Flags tab so user sees the results
        if (discoveries.length > 0) switchTab('flags');
    } catch (e) {
        console.error('TP discovery error:', e);
        showToast('TP discovery failed', 'error');
        if (statusEl) { statusEl.textContent = 'Error'; statusEl.style.color = '#ff6b6b'; }
    } finally {
        if (tpBtn) tpBtn.disabled = false;
    }
}

async function confirmAutoFlag(pipeline, type, id) {
    const key = type === 'node' ? `auto_entity_${pipeline}_${id}` : `auto_${pipeline}_${id}`;
    const entry = feedbackState[key];
    if (!entry) return;
    // Direct confirm: submit FP using the AI's reasons without opening a modal
    const reasons = entry.reasons || [];
    const reasonDetail = entry.reason_detail || '';
    try {
        if (type === 'node') {
            const visId = getVisNodeId(id);
            const meta = (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][id]) ||
                         (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][String(id)]);
            const correctedType = entry.corrected_type || '';
            const resolveToEntity = entry.resolve_to_entity || '';
            const resp = await fetch('/api/feedback/false_positive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    document_id: state.docId || '', pipeline,
                    feedback_target: 'entity',
                    subject_text: meta ? meta.text : '', subject_type: meta ? meta.entity_type : '',
                    predicate: '', object_text: '', object_type: '',
                    confidence: meta ? (meta.confidence || 0) : 0,
                    evidence_sentence: '', extraction_method: '',
                    reasons, reason_detail: reasonDetail,
                    corrected_type: correctedType,
                    resolve_to_entity: resolveToEntity,
                }),
            });
            const data = await resp.json();
            delete feedbackState[key];
            feedbackState[`entity_${pipeline}_${id}`] = { type: 'entity_fp', feedbackId: data.id, label: meta ? meta.text : `Node ${id}` };
            const curNode = nodeDataSets[pipeline].get(visId);
            const upd = { id: visId, color: { background: '#ff6b6b', border: '#ff3333', highlight: { background: '#ff6b6b', border: '#ff3333' } } };
            if (curNode) { upd.x = curNode.x; upd.y = curNode.y; upd.fixed = { x: true, y: true }; }
            nodeDataSets[pipeline].update(upd);
            if (curNode) setTimeout(() => { nodeDataSets[pipeline].update({ id: visId, fixed: { x: false, y: false } }); }, 500);
        } else {
            const edgeMeta = (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][id]);
            const resp = await fetch('/api/feedback/false_positive', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    document_id: state.docId || '', pipeline,
                    subject_text: edgeMeta ? edgeMeta.subject_text : '',
                    subject_type: '', predicate: edgeMeta ? edgeMeta.predicate : '',
                    object_text: edgeMeta ? edgeMeta.object_text : '', object_type: '',
                    confidence: edgeMeta ? (edgeMeta.confidence || 0) : 0,
                    evidence_sentence: edgeMeta ? (edgeMeta.evidence_text || '') : '',
                    extraction_method: edgeMeta ? (edgeMeta.extraction_method || '') : '',
                    reasons, reason_detail: reasonDetail,
                }),
            });
            const data = await resp.json();
            delete feedbackState[key];
            const lbl = edgeMeta ? `${edgeMeta.subject_text} → ${edgeMeta.predicate} → ${edgeMeta.object_text}` : `Edge ${id}`;
            feedbackState[`kgenskills_${id}`] = { type: 'fp', feedbackId: data.id, label: lbl };
            edgeDataSets[pipeline].update({ id, color: { color: '#ff6b6b', highlight: '#ff6b6b' } });
        }
        showToast('AI flag confirmed as FP', 'fp');
        renderFlagExplorer();
    } catch (e) {
        console.error('confirmAutoFlag error:', e);
        showToast('Error confirming flag', 'error');
    }
}

function confirmAutoFlagWithEdits(pipeline, type, id) {
    const key = type === 'node' ? `auto_entity_${pipeline}_${id}` : `auto_${pipeline}_${id}`;
    const entry = feedbackState[key];
    if (!entry) return;
    // Open the FP modal pre-filled with AI's reasons for user to edit
    if (type === 'node') {
        openEntityFPModal(pipeline, id);
        // Pre-check the AI's reason checkboxes, corrected_type, and noun action
        setTimeout(() => {
            const reasons = entry.reasons || [];
            document.querySelectorAll('#entity-fp-reasons input[type="checkbox"]').forEach(cb => {
                // resolvable_descriptor maps to not_a_proper_noun checkbox + dropdown
                if (cb.value === 'not_a_proper_noun') {
                    cb.checked = reasons.includes('not_a_proper_noun') || reasons.includes('resolvable_descriptor');
                } else {
                    cb.checked = reasons.includes(cb.value);
                }
            });
            document.getElementById('entity-fp-reason-detail').value = entry.reason_detail || '';
            if (entry.corrected_type && reasons.includes('wrong_entity_type')) {
                document.getElementById('entity-fp-corrected-type').value = entry.corrected_type;
            }
            // Pre-fill noun action dropdown
            if (reasons.includes('resolvable_descriptor')) {
                document.getElementById('entity-fp-noun-action').value = 'resolvable_descriptor';
            } else if (reasons.includes('not_a_proper_noun')) {
                document.getElementById('entity-fp-noun-action').value = 'do_not_extract';
            }
            updateEntityFPSubmitState();
        }, 50);
    } else {
        openFPModal(pipeline, id);
        setTimeout(() => {
            const reasons = entry.reasons || [];
            document.querySelectorAll('#fp-reasons input[type="checkbox"]').forEach(cb => {
                cb.checked = reasons.includes(cb.value);
            });
            document.getElementById('fp-reason-detail').value = entry.reason_detail || '';
            updateFPSubmitState();
        }, 50);
    }
    // Store the auto-flag key so modal submission can remove it
    if (type === 'node') {
        entityFPContext._autoFlagKey = key;
    } else {
        window._autoEdgeFlagKey = key;
    }
}

function dismissAutoFlag(pipeline, type, id) {
    const key = type === 'node' ? `auto_entity_${pipeline}_${id}` : `auto_${pipeline}_${id}`;
    delete feedbackState[key];
    if (type === 'node') {
        const visId = getVisNodeId(id);
        const node = nodeDataSets[pipeline].get(visId);
        const metaDismiss = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][id]);
        const entityType = metaDismiss ? metaDismiss.entity_type : '';
        const origColor = TYPE_COLORS[entityType] || '#AAA';
        nodeDataSets[pipeline].update({ id: visId, color: { background: origColor, border: origColor, highlight: { background: origColor, border: origColor } } });
    } else {
        const edge = edgeDataSets[pipeline].get(id);
        const pred = edge && edge.metadata ? edge.metadata.predicate : '';
        const origColor = REL_COLORS[pred] || '#AAA';
        edgeDataSets[pipeline].update({ id: id, color: { color: origColor, highlight: origColor } });
    }
    closeDetailPanel();
    showToast('Auto-flag dismissed', 'info');
    renderFlagExplorer();
}

// Sprint 120: Confirm/dismiss auto-discovered TPs
async function confirmAutoTP(key) {
    const entry = feedbackState[key];
    if (!entry) return;
    try {
        const isEntity = entry.discovery_type === 'entity';
        // These are EXISTING graph items confirmed as gold — save as True Positive
        const body = {
            document_id: state.docId || '',
            pipeline: entry.pipeline || '',
            subject_text: isEntity ? entry.entity_text : entry.subject_text,
            subject_type: isEntity ? entry.entity_type : '',
            confidence: entry.confidence || 0,
        };
        if (!isEntity) {
            body.predicate = entry.predicate;
            body.object_text = entry.object_text;
        }
        const resp = await fetch('/api/feedback/true_positive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (resp.status !== 200) {
            showToast(data.error || 'Failed to save gold data', 'error');
            return;
        }
        // Move from auto to confirmed TP
        const tpKey = `entity_${entry.pipeline}_${data.id}`;
        feedbackState[tpKey] = { type: 'entity_tp', label: entry.label, feedbackId: data.id };
        delete feedbackState[key];
        // Highlight the confirmed node/edge in green
        if (isEntity && entry.node_id != null) {
            const visId = getVisNodeId(entry.node_id);
            const curNode = nodeDataSets[entry.pipeline] && nodeDataSets[entry.pipeline].get(visId);
            const upd = { id: visId, color: { background: '#5ED68A', border: '#3CB371', highlight: { background: '#5ED68A', border: '#3CB371' } } };
            if (curNode) { upd.x = curNode.x; upd.y = curNode.y; upd.fixed = { x: true, y: true }; }
            if (nodeDataSets[entry.pipeline]) nodeDataSets[entry.pipeline].update(upd);
            if (curNode) setTimeout(() => { nodeDataSets[entry.pipeline].update({ id: visId, fixed: { x: false, y: false } }); }, 500);
        } else if (!isEntity && entry.edge_id != null) {
            if (edgeDataSets[entry.pipeline]) edgeDataSets[entry.pipeline].update({ id: entry.edge_id, color: { color: '#5ED68A', highlight: '#5ED68A' } });
        }
        showToast('Confirmed as gold data', 'success');
        renderFlagExplorer();
    } catch (e) {
        console.error('Confirm auto-TP error:', e);
        showToast('Failed to save', 'error');
    }
}

function dismissAutoTP(key) {
    delete feedbackState[key];
    showToast('Discovery dismissed', 'info');
    renderFlagExplorer();
}

// === Bulk auto-flag actions ===
function toggleAllAutoFlags(checked) {
    document.querySelectorAll('.auto-flag-checkbox').forEach(cb => { cb.checked = checked; });
}

function getSelectedAutoFlagKeys() {
    return Array.from(document.querySelectorAll('.auto-flag-checkbox:checked')).map(cb => cb.dataset.key);
}

async function bulkConfirmAutoFlags() {
    const keys = getSelectedAutoFlagKeys();
    if (keys.length === 0) { showToast('No flags selected', 'info'); return; }
    for (const key of keys) {
        if (key.startsWith('auto_tp_')) {
            await confirmAutoTP(key);
        } else {
            const parsed = parseFlagKey(key);
            await confirmAutoFlag(parsed.pipeline, parsed.isNode ? 'node' : 'edge', parsed.id);
        }
    }
}

function bulkDismissAutoFlags() {
    const keys = getSelectedAutoFlagKeys();
    if (keys.length === 0) { showToast('No flags selected', 'info'); return; }
    for (const key of keys) {
        if (key.startsWith('auto_tp_')) {
            dismissAutoTP(key);
        } else {
            const parsed = parseFlagKey(key);
            dismissAutoFlag(parsed.pipeline, parsed.isNode ? 'node' : 'edge', parsed.id);
        }
    }
}

async function bulkRetractAll() {
    const docFilter = (document.getElementById('stored-feedback-doc-filter') || {}).value || '';
    const msg = docFilter
        ? `Retract ALL saved feedback for document "${docFilter}"? This cannot be undone.`
        : 'Retract ALL saved feedback across ALL documents? This cannot be undone.';
    if (!confirm(msg)) return;
    try {
        const body = {};
        if (docFilter) body.document_id = docFilter;
        const resp = await fetch('/api/feedback/bulk_retract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        showToast(`Retracted ${data.retracted_count} feedback entries`, 'success');
        loadStoredFeedback();
    } catch (e) {
        showToast('Bulk retract failed: ' + e.message, 'error');
    }
}

// === Sprint 39.2 Item 6: Flag Explorer ===
function renderFlagExplorer() {
    const fpList = document.getElementById('flag-list-fp');
    const tpList = document.getElementById('flag-list-tp');
    const fnList = document.getElementById('flag-list-fn');
    const autoList = document.getElementById('flag-list-auto');
    if (!fpList || !fnList || !autoList) return;

    const fpItems = [];
    const fnItems = [];
    const tpItems = [];
    const autoItems = [];

    for (const [key, entry] of Object.entries(feedbackState)) {
        if (entry.type === 'auto_fp' || entry.type === 'auto_entity_fp' || entry.type === 'auto_edge_fp' || entry.type === 'auto_tp') {
            autoItems.push({ key, entry });
        } else if (entry.type === 'entity_tp') {
            tpItems.push({ key, entry });
        } else if (entry.type === 'fp' || entry.type === 'entity_fp') {
            fpItems.push({ key, entry });
        } else if (entry.type === 'fn' || entry.type === 'entity_fn') {
            fnItems.push({ key, entry });
        }
    }

    fpList.innerHTML = fpItems.length
        ? fpItems.map(({ key, entry }) => renderFlagItem(key, entry)).join('')
        : '<p style="color:#666;font-size:13px;">No false positives flagged.</p>';
    if (tpList) {
        tpList.innerHTML = tpItems.length
            ? tpItems.map(({ key, entry }) => renderFlagItem(key, entry)).join('')
            : '<p style="color:#666;font-size:13px;">No entities confirmed as correct.</p>';
    }
    fnList.innerHTML = fnItems.length
        ? fnItems.map(({ key, entry }) => renderFlagItem(key, entry)).join('')
        : '<p style="color:#666;font-size:13px;">No misses confirmed.</p>';
    if (autoItems.length) {
        const bulkBar = `<div style="display:flex;gap:6px;margin-bottom:8px;align-items:center;">
            <label style="font-size:12px;color:#aaa;cursor:pointer;display:flex;align-items:center;gap:4px;">
                <input type="checkbox" id="auto-flag-select-all" data-change-action="toggle-all-auto-flags" style="width:14px;height:14px;cursor:pointer;">All
            </label>
            <button data-action="bulk-confirm-auto-flags" style="padding:3px 10px;background:#2a3a2a;color:#4DD4C0;border:1px solid #3a5a3a;border-radius:4px;cursor:pointer;font-size:11px;">Confirm Selected</button>
            <button data-action="bulk-dismiss-auto-flags" style="padding:3px 10px;background:#2a2a2e;color:#aaa;border:1px solid #3a3a5e;border-radius:4px;cursor:pointer;font-size:11px;">Dismiss Selected</button>
        </div>`;
        autoList.innerHTML = bulkBar + autoItems.map(({ key, entry }) => renderFlagItem(key, entry)).join('');
    } else {
        autoList.innerHTML = '<p style="color:#666;font-size:13px;">No AI discoveries. Use "Discover FPs" / "Discover TPs" buttons on each graph toolbar.</p>';
    }

    // Update badge
    const total = fpItems.length + fnItems.length + tpItems.length + autoItems.length;
    const badge = document.getElementById('flag-count-badge');
    if (badge) {
        badge.textContent = total;
        badge.style.display = total > 0 ? 'inline-block' : 'none';
    }
}

function renderFlagItem(key, entry) {
    const parsed = parseFlagKey(key);
    const typeColors = { fp: '#ff6b6b', entity_fp: '#ff6b6b', fn: '#d4a017', entity_fn: '#d4a017', entity_tp: '#5ED68A', auto_fp: '#FF8C00', auto_entity_fp: '#FF8C00', auto_edge_fp: '#FF8C00', auto_tp: '#5ED68A' };
    const typeLabels = { fp: 'FP Edge', entity_fp: 'FP Entity', fn: 'FN Edge', entity_fn: 'FN Entity', entity_tp: 'TP Entity', auto_fp: 'AI Suggested', auto_entity_fp: 'AI Entity', auto_edge_fp: 'AI Edge', auto_tp: 'AI Gold' };
    const color = typeColors[entry.type] || '#aaa';
    const label = typeLabels[entry.type] || entry.type;

    let detailHtml = '';
    if (entry.entity_type) detailHtml += `<span style="font-size:10px;color:#4DD4C0;background:#4DD4C022;border:1px solid #4DD4C044;border-radius:3px;padding:1px 5px;margin-right:4px;">${entry.entity_type}</span>`;
    if (entry.corrected_type) detailHtml += `<span style="font-size:10px;color:#FFB347;background:#FFB34722;border:1px solid #FFB34744;border-radius:3px;padding:1px 5px;margin-right:4px;">&rarr; ${entry.corrected_type}</span>`;
    if (entry.resolve_to_entity) detailHtml += `<span style="font-size:10px;color:#4DD4C0;background:#4DD4C022;border:1px solid #4DD4C044;border-radius:3px;padding:1px 5px;margin-right:4px;">Resolve to: ${escapeHtml(entry.resolve_to_entity)}</span>`;
    if (entry.reason_detail) detailHtml += `<div style="font-size:11px;color:#aaa;margin-top:2px;">${escapeHtml(entry.reason_detail)}</div>`;
    if (entry.reasons && entry.reasons.length) {
        const reasonLabels = { not_a_proper_noun: 'Not a Proper Noun (Do Not Extract)', resolvable_descriptor: 'Not a Proper Noun (Resolvable Descriptor)', wrong_entity_type: 'Wrong Entity Type', should_resolve_to: 'Should Resolve To Existing Entity' };
        const displayReasons = entry.reasons.map(r => reasonLabels[r] || r).join(', ');
        detailHtml = `<div style="font-size:11px;color:#aaa;margin-top:2px;">${escapeHtml(displayReasons)}</div>` + detailHtml;
    }
    if (entry.evidence_sentence) {
        const escapedEvidence = escapeHtml(entry.evidence_sentence);
        const truncEvidence = entry.evidence_sentence.length > 120 ? escapeHtml(entry.evidence_sentence.substring(0, 120)) + '...' : escapedEvidence;
        detailHtml += `<div style="font-size:10px;color:#888;margin-top:3px;font-style:italic;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${entry.evidence_sentence.replace(/"/g, '&quot;')}">Context: "${truncEvidence}"</div>`;
    }

    const isAuto = entry.type === 'auto_fp' || entry.type === 'auto_entity_fp' || entry.type === 'auto_edge_fp';
    const isAutoTP = entry.type === 'auto_tp';
    const typeArg = parsed.isNode ? 'node' : 'edge';

    let actionHtml = '';
    if (isAutoTP) {
        actionHtml += `<button data-action="confirm-auto-tp" data-key="${escapeHtml(key)}" style="padding:4px 10px;background:#2a3a2a;color:#d4a017;border:1px solid #4a5a20;border-radius:4px;cursor:pointer;font-size:11px;">Confirm Gold</button>`;
        actionHtml += ` <button data-action="dismiss-auto-tp" data-key="${escapeHtml(key)}" style="padding:4px 10px;background:#2a2a2e;color:#aaa;border:1px solid #3a3a5e;border-radius:4px;cursor:pointer;font-size:11px;">Dismiss</button>`;
    } else {
        actionHtml = `<button data-action="go-to-flag" data-pipeline="${parsed.pipeline}" data-flag-type="${typeArg}" data-flag-id="${parsed.id}" style="padding:4px 10px;background:${color}22;color:${color};border:1px solid ${color}44;border-radius:4px;cursor:pointer;font-size:11px;">Go To</button>`;
        if (isAuto) {
            actionHtml += ` <button data-action="confirm-auto-flag" data-pipeline="${parsed.pipeline}" data-flag-type="${typeArg}" data-flag-id="${parsed.id}" style="padding:4px 10px;background:#2a3a2a;color:#4DD4C0;border:1px solid #3a5a3a;border-radius:4px;cursor:pointer;font-size:11px;">Confirm</button>`;
            actionHtml += ` <button data-action="confirm-auto-flag-with-edits" data-pipeline="${parsed.pipeline}" data-flag-type="${typeArg}" data-flag-id="${parsed.id}" style="padding:4px 10px;background:#2a3a2a;color:#d4a017;border:1px solid #3a5a3a;border-radius:4px;cursor:pointer;font-size:11px;">Edit</button>`;
            actionHtml += ` <button data-action="dismiss-auto-flag" data-pipeline="${parsed.pipeline}" data-flag-type="${typeArg}" data-flag-id="${parsed.id}" style="padding:4px 10px;background:#2a2a2e;color:#aaa;border:1px solid #3a3a5e;border-radius:4px;cursor:pointer;font-size:11px;">Dismiss</button>`;
        }
    }

    const checkboxHtml = (isAuto || isAutoTP) ? `<input type="checkbox" class="auto-flag-checkbox" data-key="${key}" style="width:16px;height:16px;cursor:pointer;">` : '';

    return `<div style="padding:8px 12px;background:#1a1a2e;border:1px solid #3a3a5e;border-radius:6px;margin-bottom:6px;display:flex;align-items:center;gap:10px;">
        ${checkboxHtml}
        <span style="background:${color}22;color:${color};border:1px solid ${color}44;border-radius:4px;padding:2px 8px;font-size:11px;white-space:nowrap;">${label}</span>
        <div style="flex:1;min-width:0;">
            <div style="font-size:13px;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(entry.label || key)}</div>
            ${detailHtml}
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;">${actionHtml}</div>
    </div>`;
}

function parseFlagKey(key) {
    // Keys: `${pipeline}_${edgeId}`, `entity_${pipeline}_${nodeId}`, `entity_fn_${pipeline}_${nodeId}`,
    //        `auto_${pipeline}_${edgeId}`, `auto_entity_${pipeline}_${nodeId}`, `auto_tp_${pipeline}_...`
    if (key.startsWith('auto_tp_')) {
        // auto_tp_slot-N_subj_pred_obj — pipeline is slot-N (contains hyphen)
        const rest = key.slice('auto_tp_'.length);
        const pipelineMatch = rest.match(/^(slot-\d+)_/);
        const pipeline = pipelineMatch ? pipelineMatch[1] : rest.split('_')[0];
        return { pipeline, id: key, isNode: false };
    }
    if (key.startsWith('auto_entity_')) {
        const rest = key.slice('auto_entity_'.length);
        const sep = rest.indexOf('_');
        return { pipeline: rest.slice(0, sep), id: parseInt(rest.slice(sep + 1)), isNode: true };
    }
    if (key.startsWith('auto_')) {
        const rest = key.slice('auto_'.length);
        const sep = rest.indexOf('_');
        return { pipeline: rest.slice(0, sep), id: rest.slice(sep + 1), isNode: false };
    }
    if (key.startsWith('entity_fn_')) {
        const rest = key.slice('entity_fn_'.length);
        const sep = rest.indexOf('_');
        return { pipeline: rest.slice(0, sep), id: parseInt(rest.slice(sep + 1)), isNode: true };
    }
    if (key.startsWith('entity_')) {
        const rest = key.slice('entity_'.length);
        const sep = rest.indexOf('_');
        return { pipeline: rest.slice(0, sep), id: parseInt(rest.slice(sep + 1)), isNode: true };
    }
    const sep = key.indexOf('_');
    return { pipeline: key.slice(0, sep), id: key.slice(sep + 1), isNode: false };
}

function goToFlag(pipeline, type, id) {
    switchTab('compare');
    setTimeout(() => {
        if (type === 'node') {
            navigateToNode(pipeline, id);
        } else {
            navigateToEdge(pipeline, id);
        }
    }, 100);
}

// ============================================================
// Sprint 48: Load stored feedback from JSON files
// ============================================================
let _storedFeedbackLoaded = false;

async function loadStoredFeedback() {
    const fpList = document.getElementById('stored-fp-list');
    const fnList = document.getElementById('stored-fn-list');
    if (!fpList || !fnList) return;

    const docFilter = (document.getElementById('stored-feedback-doc-filter') || {}).value || '';
    const url = docFilter ? `/api/feedback/list?document_id=${encodeURIComponent(docFilter)}` : '/api/feedback/list';

    try {
        const resp = await fetch(url);
        const data = await resp.json();
        const fps = data.false_positives || [];
        const fns = data.false_negatives || [];

        fpList.innerHTML = fps.length
            ? fps.map(fp => renderStoredFeedbackItem(fp, 'fp')).join('')
            : '<p style="color:#666;font-size:13px;">No stored false positives.</p>';
        fnList.innerHTML = fns.length
            ? fns.map(fn => renderStoredFeedbackItem(fn, 'fn')).join('')
            : '<p style="color:#666;font-size:13px;">No stored false negatives.</p>';

        // Update badge to include stored count
        const sessionTotal = Object.keys(feedbackState).length;
        const storedTotal = fps.length + fns.length;
        const badge = document.getElementById('flag-count-badge');
        if (badge) {
            const total = sessionTotal + storedTotal;
            badge.textContent = total;
            badge.style.display = total > 0 ? 'inline-block' : 'none';
        }
        _storedFeedbackLoaded = true;
    } catch (e) {
        console.error('loadStoredFeedback error:', e);
        fpList.innerHTML = '<p style="color:#ff6b6b;font-size:13px;">Error loading stored feedback.</p>';
        fnList.innerHTML = '';
    }
}

function renderStoredFeedbackItem(entry, feedbackType) {
    const color = feedbackType === 'fp' ? '#ff6b6b' : '#d4a017';
    const target = entry.feedback_target || 'relationship';
    const label = feedbackType === 'fp'
        ? (target === 'entity' ? 'FP Entity' : 'FP Edge')
        : (target === 'entity' ? 'FN Entity' : 'FN Edge');

    // Build display label
    let displayLabel = '';
    if (target === 'entity') {
        displayLabel = entry.subject_text || '?';
    } else {
        displayLabel = `${entry.subject_text || '?'} → ${entry.predicate || '?'} → ${entry.object_text || '?'}`;
    }

    let detailHtml = '';
    // Entity type
    if (entry.subject_type) detailHtml += `<span style="font-size:10px;color:#4DD4C0;background:#4DD4C022;border:1px solid #4DD4C044;border-radius:3px;padding:1px 5px;margin-right:4px;">${entry.subject_type}</span>`;
    // Corrected type
    if (entry.corrected_type) detailHtml += `<span style="font-size:10px;color:#FFB347;background:#FFB34722;border:1px solid #FFB34744;border-radius:3px;padding:1px 5px;margin-right:4px;">&rarr; ${entry.corrected_type}</span>`;
    // Document
    if (entry.document_id) detailHtml += `<span style="font-size:10px;color:#6B8FFF;background:#6B8FFF22;border:1px solid #6B8FFF44;border-radius:3px;padding:1px 5px;margin-right:4px;">${entry.document_id}</span>`;
    // Flagged by
    if (entry.flagged_by) detailHtml += `<span style="font-size:10px;color:#999;background:#99999922;border:1px solid #99999944;border-radius:3px;padding:1px 5px;">${entry.flagged_by}</span>`;
    // Reasons
    if (entry.reasons && entry.reasons.length) {
        const reasonLabels = { not_a_proper_noun: 'Not a Proper Noun (Do Not Extract)', resolvable_descriptor: 'Not a Proper Noun (Resolvable Descriptor)', wrong_entity_type: 'Wrong Entity Type', should_resolve_to: 'Should Resolve To Existing Entity' };
        const displayReasons = entry.reasons.map(r => reasonLabels[r] || r).join(', ');
        detailHtml = `<div style="font-size:11px;color:#aaa;margin-top:2px;">${displayReasons}</div>` + detailHtml;
    }
    // Reason detail
    if (entry.reason_detail) detailHtml += `<div style="font-size:11px;color:#aaa;margin-top:2px;">${entry.reason_detail}</div>`;
    // Evidence sentence
    if (entry.evidence_sentence) {
        const ev = entry.evidence_sentence;
        const evDisplay = ev.length > 120 ? ev.substring(0, 120) + '...' : ev;
        detailHtml += `<div style="font-size:10px;color:#888;margin-top:3px;font-style:italic;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${ev.replace(/"/g, '&quot;')}">Context: "${evDisplay}"</div>`;
    }

    const feedbackId = entry.id || '';
    const retractBtn = `<button data-action="retract-stored-feedback" data-feedback-id="${escapeHtml(feedbackId)}" style="padding:4px 10px;background:#2a2a2e;color:#ff6b6b;border:1px solid #3a3a5e;border-radius:4px;cursor:pointer;font-size:11px;">Retract</button>`;

    return `<div style="padding:8px 12px;background:#1a1a2e;border:1px solid #3a3a5e;border-radius:6px;margin-bottom:6px;display:flex;align-items:center;gap:10px;">
        <span style="background:${color}22;color:${color};border:1px solid ${color}44;border-radius:4px;padding:2px 8px;font-size:11px;white-space:nowrap;">${label}</span>
        <div style="flex:1;min-width:0;">
            <div style="font-size:13px;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${displayLabel}</div>
            ${detailHtml}
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;">${retractBtn}</div>
    </div>`;
}

async function retractStoredFeedback(feedbackId, btn) {
    if (!feedbackId) return;
    try {
        btn.disabled = true;
        btn.textContent = '...';
        const resp = await fetch('/api/feedback/retract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback_id: feedbackId }),
        });
        if (resp.ok) {
            showToast('Feedback retracted', 'info');
            loadStoredFeedback();
        } else {
            showToast('Retraction failed', 'error');
            btn.disabled = false;
            btn.textContent = 'Retract';
        }
    } catch (e) {
        console.error('retractStoredFeedback error:', e);
        showToast('Retraction failed', 'error');
        btn.disabled = false;
        btn.textContent = 'Retract';
    }
}


// --- compare.html lines 4328-4410: gemRefresh ---
function gemRefresh() {
    if (!gemRunState.docId) return;
    let refreshDone = false;  // Sprint 33.10: SSE completion flag — guards onerror on normal close
    // Sprint 33.9b: Disable button during refresh to prevent double-clicks
    const gemRefreshBtn = document.getElementById('gem-refresh');
    gemRefreshBtn.disabled = true;
    // Sprint 33.4: Per-column refresh — re-run only LLM Full Shot
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const gemModel = document.getElementById('model-select').value;
    const refreshUrl = currentDomain === 'clinical'
        ? `/api/compare-clinical/${gemRunState.docId}?force_refresh=gemini&model=${gemModel}`
        : `/api/refresh-agentic-flash/${gemRunState.docId}?corpus_kb=${corpusKb}&model=${gemModel}`;
    const refreshSource = new EventSource(refreshUrl);
    document.getElementById('gemini-graph').innerHTML = '<div class="placeholder">Re-extracting...</div>';
    document.getElementById('gemini-stats').innerHTML = '';
    document.getElementById('gemini-legend').innerHTML = '';
    document.getElementById('gemini-rel-legend').innerHTML = '';
    // Sprint 33.5: Timeline step for refresh
    document.getElementById('compare-timeline').style.display = 'block';
    addTimelineStep('compare', 'gem-refresh', 'LLM Full Shot (refresh)', 'running');

    refreshSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('compare', 'gem-refresh', d.label, d.duration_ms, d.tokens);
    });
    refreshSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        if (d.pipeline && d.pipeline !== 'gemini') return;  // Skip non-gemini events
        renderGraph('gemini', d.vis, d.stats);
        const statsEl = document.getElementById('gemini-stats');
        statsEl.innerHTML = `${d.stats.entities} entities | ${d.stats.relationships} rels | ${d.stats.tokens.toLocaleString()} tokens | ${(d.stats.duration_ms / 1000).toFixed(1)}s`;
        if (d.stats.throughput_kb_sec) {
            statsEl.innerHTML += `<div style="color:#5ED68A; font-size:11px; margin-top:4px;">&#9889; ${d.stats.throughput_kb_sec.toFixed(1)} KB/sec</div>`;
        }
        if (d.errors > 0) {
            statsEl.innerHTML += `<div class="error-badge">\u26A0 ${d.errors} Throttled by Provider <button class="retry-btn" data-action="gem-refresh">&#8635; Retry</button></div>`;
        }
        if (d.truncated) {
            statsEl.innerHTML += `<div class="error-badge" style="background:#3a2a15; border-color:#5a4a20;">\u26A0 LLM Output Truncated: Graph incomplete \u2014 try a more powerful model. <button class="retry-btn" data-action="gem-refresh" style="margin-left:8px;">&#8635; Retry</button></div>`;
        }
        if (typeof d.total_runs === 'number') {
            gemRunState.currentIndex = 0;
            gemRunState.totalRuns = d.total_runs;
            updateGemRunUI();
            document.getElementById('gem-run-meta').textContent = d.model ? `${d.model} (live)` : 'live extraction';
        }
        // Update audit table
        const tokens = d.stats.tokens || 0;
        document.getElementById('audit-fullshot-tokens').textContent = tokens.toLocaleString();
        const cost = calcCost(tokens);
        setCostCell('audit-fullshot-cost', tokens > 0 ? cost : 0, costRateLabel());
        document.getElementById('audit-fullshot-errors').textContent = d.errors || 0;
        if (d.stats.duration_ms && d.stats.actual_kb) {
            document.getElementById('audit-fullshot-throughput').textContent = (d.stats.actual_kb / (d.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
        }
        state.compare.vis_gem = d.vis;
        state.compare.stats_gem = d.stats;
        updateComparisonMatrix();
    });
    refreshSource.addEventListener('error', (e) => {
        refreshDone = true;
        if (e.data) {
            const d = JSON.parse(e.data);
            updateStepState('compare', 'gem-refresh', d.message || 'LLM Full Shot failed', 'error');
            document.getElementById('gemini-graph').innerHTML =
                `<div class="placeholder">${d.message || 'Extraction failed'}` +
                `<br><button class="retry-btn" data-action="gem-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
        }
        gemRefreshBtn.disabled = false;
    });
    refreshSource.addEventListener('done', () => { refreshDone = true; refreshSource.close(); gemRefreshBtn.disabled = false; });
    refreshSource.onerror = () => {
        if (refreshDone) return;  // Sprint 33.10: SSE spec fires onerror on normal close
        updateStepState('compare', 'gem-refresh', 'LLM Full Shot \u2717', 'error');
        document.getElementById('gemini-graph').innerHTML =
            `<div class="placeholder">Connection lost` +
            `<br><button class="retry-btn" data-action="gem-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
        refreshSource.close();
        gemRefreshBtn.disabled = false;
    };
}

// Sprint 33.2: Modular (LLM Multi-Stage) run history — mirrors Full Shot history

// --- compare.html lines 4482-4593: modRefresh ---
function modRefresh() {
    if (!modRunState.docId) return;
    let refreshDone = false;  // Sprint 33.10: SSE completion flag — guards onerror on normal close
    // Sprint 33.9b: Disable button during refresh to prevent double-clicks
    const modRefreshBtn = document.getElementById('mod-refresh');
    modRefreshBtn.disabled = true;
    // Sprint 33.4: Per-column refresh — re-run only LLM Multi-Stage
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const chunkSize = document.getElementById('chunk-size-select').value;
    const gemModel = document.getElementById('model-select').value;
    const refreshUrl = currentDomain === 'clinical'
        ? `/api/compare-clinical/${modRunState.docId}?force_refresh=modular&model=${gemModel}&chunk_size=${chunkSize}`
        : `/api/refresh-agentic-analyst/${modRunState.docId}?corpus_kb=${corpusKb}&chunk_size=${chunkSize}&model=${gemModel}`;
    const refreshSource = new EventSource(refreshUrl);
    document.getElementById('modular-graph').innerHTML = '<div class="placeholder">Re-extracting...</div>';
    document.getElementById('modular-stats').innerHTML = '';
    document.getElementById('modular-legend').innerHTML = '';
    document.getElementById('modular-rel-legend').innerHTML = '';
    // Sprint 33.5: Timeline step for refresh
    document.getElementById('compare-timeline').style.display = 'block';
    addTimelineStep('compare', 'mod-refresh', 'LLM Multi-Stage (refresh)', 'running');

    refreshSource.addEventListener('step_progress', (e) => {
        const d = JSON.parse(e.data);
        const bar = document.getElementById('modular-progress-bar');
        const lbl = document.getElementById('modular-progress-label');
        document.getElementById('modular-progress').style.display = 'block';
        lbl.textContent = d.label;
        bar.style.width = `${(d.progress / d.total * 100).toFixed(0)}%`;
        // Sprint 33.6: Show cancel button
        document.getElementById('mod-cancel').style.display = 'inline-block';
        updateStepProgress('compare', 'mod-refresh', d.progress, d.total, d.label);
    });
    refreshSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('compare', 'mod-refresh', d.label, d.duration_ms, d.tokens);
    });
    refreshSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        if (d.pipeline && d.pipeline !== 'modular') return;  // Skip non-modular events
        document.getElementById('modular-progress').style.display = 'none';
        document.getElementById('mod-cancel').style.display = 'none';
        renderGraph('modular', d.vis, d.stats);
        const statsEl = document.getElementById('modular-stats');
        statsEl.innerHTML = `${d.stats.entities} entities | ${d.stats.relationships} rels | ${d.stats.tokens.toLocaleString()} tokens | ${(d.stats.duration_ms / 1000).toFixed(1)}s`;
        if (d.stats.throughput_kb_sec) {
            statsEl.innerHTML += `<div style="color:#5ED68A; font-size:11px; margin-top:4px;">&#9889; ${d.stats.throughput_kb_sec.toFixed(1)} KB/sec</div>`;
        }
        if (d.errors > 0) {
            statsEl.innerHTML += `<div class="error-badge">\u26A0 ${d.errors} Throttled by Provider <button class="retry-btn" data-action="mod-refresh">&#8635; Retry</button></div>`;
        }
        // Sprint 33.6: Partial results badge
        if (d.stats.chunks_completed && d.stats.chunks_total && d.stats.chunks_completed < d.stats.chunks_total) {
            const pct = Math.round(d.stats.chunks_completed / d.stats.chunks_total * 100);
            statsEl.innerHTML += `<div class="partial-badge">\u26A0 Partial: ${d.stats.chunks_completed}/${d.stats.chunks_total} chunks (${pct}%)</div>`;
        }
        if (typeof d.total_runs === 'number') {
            modRunState.currentIndex = 0;
            modRunState.totalRuns = d.total_runs;
            updateModRunUI();
            document.getElementById('mod-run-meta').textContent = d.model ? `${d.model} (live)` : 'live extraction';
        }
        // Update audit table — Sprint 33.6: with partial inflation
        let tokens = d.stats.tokens || 0;
        let displayCost = calcCost(tokens);
        let displayDuration = d.stats.duration_ms || 0;
        let estSuffix = '';
        if (d.stats.chunks_completed && d.stats.chunks_total && d.stats.chunks_completed < d.stats.chunks_total) {
            const scale = d.stats.chunks_total / d.stats.chunks_completed;
            tokens = Math.round(tokens * scale);
            displayCost = displayCost * scale;
            displayDuration = displayDuration * scale;
            estSuffix = ' (est.)';
        }
        document.getElementById('audit-multistage-tokens').textContent = tokens.toLocaleString() + estSuffix;
        setCostCell('audit-multistage-cost', tokens > 0 ? displayCost : 0, costRateLabel());
        document.getElementById('audit-multistage-errors').textContent = d.errors || 0;
        if (displayDuration && d.stats.actual_kb) {
            document.getElementById('audit-multistage-throughput').textContent = (d.stats.actual_kb / (displayDuration / 1000)).toFixed(1) + ' KB/s' + estSuffix;
        }
        state.compare.vis_mod = d.vis;
        state.compare.stats_mod = d.stats;
        updateComparisonMatrix();
    });
    refreshSource.addEventListener('error', (e) => {
        refreshDone = true;
        if (e.data) {
            const d = JSON.parse(e.data);
            updateStepState('compare', 'mod-refresh', d.message || 'LLM Multi-Stage failed', 'error');
            document.getElementById('modular-graph').innerHTML =
                `<div class="placeholder">${d.message || 'Extraction failed'}` +
                `<br><button class="retry-btn" data-action="mod-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
            document.getElementById('modular-progress').style.display = 'none';
            document.getElementById('mod-cancel').style.display = 'none';
        }
        modRefreshBtn.disabled = false;
    });
    refreshSource.addEventListener('done', () => { refreshDone = true; refreshSource.close(); modRefreshBtn.disabled = false; });
    refreshSource.onerror = () => {
        if (refreshDone) return;  // Sprint 33.10: SSE spec fires onerror on normal close
        updateStepState('compare', 'mod-refresh', 'LLM Multi-Stage \u2717', 'error');
        document.getElementById('modular-graph').innerHTML =
            `<div class="placeholder">Connection lost` +
            `<br><button class="retry-btn" data-action="mod-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
        refreshSource.close();
        modRefreshBtn.disabled = false;
    };
}

// ============================================================
// KGSpin Run History (Sprint 33.17 — WI-3)
// ============================================================

// --- compare.html lines 4668-4749: kgenRefresh ---
function kgenRefresh() {
    if (!kgenRunState.docId) return;
    let refreshDone = false;
    const kgenRefreshBtn = document.getElementById('kgen-refresh');
    kgenRefreshBtn.disabled = true;
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const bundleVersion = document.getElementById('bundle-select').value;
    const refreshEndpoint = currentDomain === 'clinical'
        ? `/api/compare-clinical/${kgenRunState.docId}?force_refresh=kgen&bundle=${bundleVersion}`
        : `/api/refresh-discovery/${kgenRunState.docId}?corpus_kb=${corpusKb}&bundle=${bundleVersion}`;
    const refreshUrl = refreshEndpoint;
    const refreshSource = new EventSource(refreshUrl);
    document.getElementById('kgenskills-graph').innerHTML = '<div class="placeholder">Re-extracting...</div>';
    document.getElementById('kgenskills-stats').innerHTML = '';
    document.getElementById('kgenskills-legend').innerHTML = '';
    document.getElementById('kgenskills-rel-legend').innerHTML = '';
    document.getElementById('compare-timeline').style.display = 'block';
    addTimelineStep('compare', 'kgen-refresh', 'KGSpin (refresh)', 'running');

    refreshSource.addEventListener('step_progress', (e) => {
        const d = JSON.parse(e.data);
        updateStepProgress('compare', 'kgen-refresh', d.progress, d.total, d.label);
    });
    refreshSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('compare', 'kgen-refresh', d.label, d.duration_ms, d.tokens);
    });
    refreshSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        if (d.pipeline && d.pipeline !== 'kgenskills') return;  // Skip non-KGSpin events
        renderGraph('kgenskills', d.vis, d.stats);
        const statsEl = document.getElementById('kgenskills-stats');
        const bv = d.bundle_version || '1.0';
        const qc2 = d.stats.quarantine_count || 0;
        const qBadge2 = qc2 > 0 ? ` <span style="color:#FF6B6B; font-size:11px; margin-left:4px;" title="${qc2} entities quarantined by precision sieve">&#128683; ${qc2} quarantined</span>` : '';
        statsEl.innerHTML = `${d.stats.entities} entities | ${d.stats.relationships} rels | ${(d.stats.duration_ms / 1000).toFixed(1)}s${qBadge2} <span style="color:#7B68EE; font-size:11px; margin-left:6px;">&#128230; ${bv}</span>`;
        if (d.stats.throughput_kb_sec) {
            statsEl.innerHTML += `<div style="color:#5ED68A; font-size:11px; margin-top:4px;">&#9889; ${d.stats.throughput_kb_sec.toFixed(1)} KB/sec</div>`;
        }
        // Update audit table
        document.getElementById('audit-kgen-tokens').textContent = '0';
        const cpuCost = d.stats.cpu_cost || 0;
        setCostCell('audit-kgen-cost', cpuCost, '$0.05/hr CPU');
        if (d.stats.duration_ms && d.stats.actual_kb) {
            document.getElementById('audit-kgen-throughput').textContent = (d.stats.actual_kb / (d.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
        }
        if (typeof d.total_runs === 'number') {
            kgenRunState.currentIndex = 0;
            kgenRunState.totalRuns = d.total_runs;
            updateKgenRunUI();
            document.getElementById('kgen-run-meta').textContent = 'live extraction';
        }
        state.compare.vis_kgs = d.vis;
        state.compare.stats_kgs = d.stats;
        updateComparisonMatrix();
    });
    refreshSource.addEventListener('error', (e) => {
        refreshDone = true;
        if (e.data) {
            const d = JSON.parse(e.data);
            updateStepState('compare', 'kgen-refresh', d.message || 'KGSpin failed', 'error');
            document.getElementById('kgenskills-graph').innerHTML =
                `<div class="placeholder">${d.message || 'Extraction failed'}` +
                `<br><button class="retry-btn" data-action="kgen-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
        }
        kgenRefreshBtn.disabled = false;
    });
    refreshSource.addEventListener('done', () => { refreshDone = true; refreshSource.close(); kgenRefreshBtn.disabled = false; });
    refreshSource.onerror = () => {
        if (refreshDone) return;
        updateStepState('compare', 'kgen-refresh', 'KGSpin \u2717', 'error');
        document.getElementById('kgenskills-graph').innerHTML =
            `<div class="placeholder">Connection lost` +
            `<br><button class="retry-btn" data-action="kgen-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
        refreshSource.close();
        kgenRefreshBtn.disabled = false;
    };
}

// ============================================================
// Intelligence Run History (Sprint 33.17 — WI-4)
// ============================================================

// --- compare.html lines 5120-5569: startComparison + startComparisonForTicker + highlightBest* + cancelMultistage + resetCompareUI + showSourcePanel ---
function startComparison() {
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;

    // Sprint 91: Slot-based mode — just set ticker and show slot panels
    state.docId = ticker;
    document.getElementById('welcome').style.display = 'none';
    document.getElementById('compare-content').style.display = 'block';
    document.getElementById('status').textContent = `Ready — select pipelines for ${ticker}`;

    // Reset all slots
    slotState.forEach((s, idx) => {
        s.pipeline = '';
        s.bundle = '';
        s.strategy = '';
        s.visData = null;
        s.stats = null;
        s.kg = null;
        const sel = document.getElementById(`slot-${idx}-pipeline`);
        if (sel) sel.value = '';
        const bundleSel = document.getElementById(`slot-${idx}-bundle`);
        if (bundleSel) bundleSel.style.display = 'none';
        const runBtn = document.getElementById(`slot-${idx}-run`);
        if (runBtn) runBtn.style.display = 'none';
        const graphEl = document.getElementById(`slot-${idx}-graph`);
        if (graphEl) graphEl.innerHTML = '<p style="color:#666;text-align:center;padding:40px;">Select a pipeline above</p>';
    });
    updateAnalyzeButton();

    // Load bundle options for KGSpin dropdowns
    loadBundleOptions();
}

function startComparisonForTicker(ticker, forceRefresh) {
    state.docId = ticker;
    gemRunState.docId = ticker;
    gemRunState.currentIndex = 0;
    gemRunState.totalRuns = 0;
    modRunState.docId = ticker;
    modRunState.currentIndex = 0;
    modRunState.totalRuns = 0;
    kgenRunState.docId = ticker;
    kgenRunState.currentIndex = 0;
    kgenRunState.totalRuns = 0;
    resetCompareUI();
    document.getElementById('welcome').style.display = 'none';
    document.getElementById('compare-timeline').style.display = 'block';
    document.getElementById('compare-content').style.display = 'block';
    document.getElementById('status').textContent = 'Running...';
    document.getElementById('gemini-history').style.display = 'none';
    document.getElementById('modular-history').style.display = 'none';

    if (eventSource) eventSource.close();
    const corpusKb = document.getElementById('corpus-kb-select').value;
    const chunkSize = document.getElementById('chunk-size-select').value;
    const gemModel = document.getElementById('model-select').value;
    const bundleVersion = document.getElementById('bundle-select').value;
    const params = new URLSearchParams();
    if (forceRefresh) params.set('force_refresh', '1');
    params.set('corpus_kb', corpusKb);
    params.set('model', gemModel);
    params.set('chunk_size', chunkSize);
    if (bundleVersion) params.set('bundle', bundleVersion);
    const compareEndpoint = currentDomain === 'clinical'
        ? `/api/compare-clinical/${ticker}`
        : `/api/compare/${ticker}`;
    eventSource = new EventSource(`${compareEndpoint}?${params.toString()}`);

    eventSource.onopen = () => {
        document.getElementById('status').textContent = 'Connected...';
    };

    eventSource.addEventListener('step_start', (e) => {
        const d = JSON.parse(e.data);
        addTimelineStep('compare', d.step, d.label, 'running');
        if (d.pipeline) {
            const el = document.getElementById(`${d.pipeline}-progress`);
            const lbl = document.getElementById(`${d.pipeline}-progress-label`);
            if (el) { el.style.display = 'block'; lbl.textContent = d.label; }
        }
    });

    eventSource.addEventListener('step_progress', (e) => {
        const d = JSON.parse(e.data);
        updateStepProgress('compare', d.step, d.progress, d.total, d.label);
        if (d.pipeline) {
            const el = document.getElementById(`${d.pipeline}-progress`);
            const lbl = document.getElementById(`${d.pipeline}-progress-label`);
            const bar = document.getElementById(`${d.pipeline}-progress-bar`);
            if (el) {
                el.style.display = 'block';
                lbl.textContent = d.label;
                bar.style.width = `${(d.progress / d.total * 100).toFixed(0)}%`;
            }
            // Sprint 33.6: Show cancel button for modular pipeline
            if (d.pipeline === 'modular') {
                document.getElementById('mod-cancel').style.display = 'inline-block';
            }
        }
    });

    eventSource.addEventListener('step_complete', (e) => {
        const d = JSON.parse(e.data);
        completeStep('compare', d.step, d.label, d.duration_ms, d.tokens);
        // Populate source panel from fetch_sec metadata
        if (d.step === 'fetch_sec' && d.details) {
            showSourcePanel(d.details);
        }
    });

    eventSource.addEventListener('kg_ready', (e) => {
        const d = JSON.parse(e.data);
        const el = document.getElementById(`${d.pipeline}-progress`);
        if (el) el.style.display = 'none';
        renderGraph(d.pipeline, d.vis, d.stats);
        // Sprint 79: Store document_context for metadata display
        if (d.document_context) storeDocumentContext(d.pipeline, d.document_context);
        // Sprint 33.3: Throughput badge
        const statsEl = document.getElementById(`${d.pipeline === 'kgenskills' ? 'kgenskills' : d.pipeline === 'modular' ? 'modular' : 'gemini'}-stats`);
        if (d.stats && d.stats.throughput_kb_sec) {
            statsEl.innerHTML += `<div style="color:#5ED68A; font-size:11px; margin-top:4px;">&#9889; ${d.stats.throughput_kb_sec.toFixed(1)} KB/sec</div>`;
        }
        // Sprint 33.20: CPU cost badge for KGSpin
        // Bundle version badge for KGSpin
        if (d.pipeline === 'kgenskills') {
            const bv = d.bundle_version || '1.0';
            statsEl.innerHTML += `<div style="color:#7B68EE; font-size:11px; margin-top:4px;">&#128230; Bundle: ${bv}</div>`;
        }
        // Populate combined matrix table incrementally
        if (d.pipeline === 'kgenskills') {
            document.getElementById('audit-kgen-tokens').textContent = '0';
            const cpuCost = (d.stats && d.stats.cpu_cost) ? d.stats.cpu_cost : 0;
            setCostCell('audit-kgen-cost', cpuCost, '$0.05/hr CPU');
            document.getElementById('audit-kgen-errors').textContent = '0';
            if (d.stats && d.stats.duration_ms && d.stats.actual_kb) {
                document.getElementById('audit-kgen-throughput').textContent = (d.stats.actual_kb / (d.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
            }
        } else if (d.pipeline === 'gemini') {
            const tokens = d.stats ? d.stats.tokens || 0 : 0;
            document.getElementById('audit-fullshot-tokens').textContent = tokens.toLocaleString();
            const cost = calcCost(tokens);
            setCostCell('audit-fullshot-cost', tokens > 0 ? cost : 0, costRateLabel());
            const errors = d.errors || 0;
            const errCell = document.getElementById('audit-fullshot-errors');
            errCell.textContent = errors;
            if (errors > 0) errCell.classList.add('audit-error');
            if (d.stats && d.stats.duration_ms && d.stats.actual_kb) {
                document.getElementById('audit-fullshot-throughput').textContent = (d.stats.actual_kb / (d.stats.duration_ms / 1000)).toFixed(1) + ' KB/s';
            }
        } else if (d.pipeline === 'modular') {
            document.getElementById('mod-cancel').style.display = 'none';
            let tokens = d.stats ? d.stats.tokens || 0 : 0;
            let displayCost, displayDuration;
            const rawCost = calcCost(tokens);
            displayCost = rawCost;
            displayDuration = d.stats ? d.stats.duration_ms : 0;
            let estSuffix = '';
            if (d.stats && d.stats.chunks_completed && d.stats.chunks_total && d.stats.chunks_completed < d.stats.chunks_total) {
                const scale = d.stats.chunks_total / d.stats.chunks_completed;
                tokens = Math.round(tokens * scale);
                displayCost = rawCost * scale;
                displayDuration = displayDuration * scale;
                estSuffix = ' (est.)';
            }
            document.getElementById('audit-multistage-tokens').textContent = tokens.toLocaleString() + estSuffix;
            setCostCell('audit-multistage-cost', tokens > 0 ? displayCost : 0, costRateLabel());
            const errors = d.errors || 0;
            const errCell = document.getElementById('audit-multistage-errors');
            errCell.textContent = errors;
            if (errors > 0) errCell.classList.add('audit-error');
            if (d.stats && displayDuration && d.stats.actual_kb) {
                document.getElementById('audit-multistage-throughput').textContent = (d.stats.actual_kb / (displayDuration / 1000)).toFixed(1) + ' KB/s' + estSuffix;
            }
        }
        // Persist in state
        if (d.pipeline === 'kgenskills') {
            state.compare.vis_kgs = d.vis;
            state.compare.stats_kgs = d.stats;
            // Sprint 33.17 (WI-3): KGSpin history bar
            if (typeof d.total_runs === 'number' && d.total_runs > 0) {
                kgenRunState.docId = state.docId;
                kgenRunState.currentIndex = d.run_index || 0;
                kgenRunState.totalRuns = d.total_runs;
                updateKgenRunUI();
                const metaEl = document.getElementById('kgen-run-meta');
                if (d.from_log) {
                    const ts = d.run_timestamp ? new Date(d.run_timestamp).toLocaleString() : '';
                    metaEl.textContent = `from log ${ts}`;
                } else {
                    metaEl.textContent = 'live extraction';
                }
            }
        } else if (d.pipeline === 'modular') {
            state.compare.vis_mod = d.vis;
            state.compare.stats_mod = d.stats;
            if (typeof d.total_runs === 'number' && d.total_runs > 0) {
                modRunState.currentIndex = d.run_index || 0;
                modRunState.totalRuns = d.total_runs;
                updateModRunUI();
                const metaEl = document.getElementById('mod-run-meta');
                if (d.from_log) {
                    const ts = d.run_timestamp ? new Date(d.run_timestamp).toLocaleString() : '';
                    metaEl.textContent = d.model ? `${d.model} · from log ${ts}` : `from log ${ts}`;
                } else {
                    metaEl.textContent = d.model ? `${d.model} (live)` : 'live extraction';
                }
            }
            if (d.errors > 0) {
                statsEl.innerHTML += `<div class="error-badge">\u26A0 ${d.errors} Throttled by Provider <button class="retry-btn" data-action="mod-refresh">&#8635; Retry</button></div>`;
            }
            // Sprint 33.6: Partial results badge
            if (d.stats && d.stats.chunks_completed && d.stats.chunks_total && d.stats.chunks_completed < d.stats.chunks_total) {
                const pct = Math.round(d.stats.chunks_completed / d.stats.chunks_total * 100);
                statsEl.innerHTML += `<div class="partial-badge">\u26A0 Partial: ${d.stats.chunks_completed}/${d.stats.chunks_total} chunks (${pct}%)</div>`;
            }
        } else {
            state.compare.vis_gem = d.vis;
            state.compare.stats_gem = d.stats;
            if (typeof d.total_runs === 'number' && d.total_runs > 0) {
                gemRunState.currentIndex = d.run_index || 0;
                gemRunState.totalRuns = d.total_runs;
                updateGemRunUI();
                const metaEl = document.getElementById('gem-run-meta');
                if (d.from_log) {
                    const ts = d.run_timestamp ? new Date(d.run_timestamp).toLocaleString() : '';
                    metaEl.textContent = d.model ? `${d.model} · from log ${ts}` : `from log ${ts}`;
                } else {
                    metaEl.textContent = d.model ? `${d.model} (live)` : 'live extraction';
                }
            }
            if (d.errors > 0) {
                statsEl.innerHTML += `<div class="error-badge">\u26A0 ${d.errors} Throttled by Provider <button class="retry-btn" data-action="gem-refresh">&#8635; Retry</button></div>`;
            }
            // Sprint 33.3: VP Refinement #4 — truncation warning
            if (d.truncated) {
                statsEl.innerHTML += `<div class="error-badge" style="background:#3a2a15; border-color:#5a4a20;">\u26A0 LLM Output Truncated: Graph incomplete \u2014 try a more powerful model. <button class="retry-btn" data-action="gem-refresh" style="margin-left:8px;">&#8635; Retry</button></div>`;
            }
        }
        updateComparisonMatrix();
    });

    eventSource.addEventListener('analysis_ready', (e) => {
        const d = JSON.parse(e.data);
        state.compare.analysis = d.analysis;
        renderAnalysis(d.analysis);
    });

    eventSource.addEventListener('scores_ready', (e) => {
        renderScores(JSON.parse(e.data));
    });

    eventSource.addEventListener('error', (e) => {
        if (e.data) {
            const d = JSON.parse(e.data);
            addTimelineStep('compare', d.step, d.message, 'error');
            if (!d.recoverable) {
                document.getElementById('status').textContent = 'Error';
            }
            // Sprint 33.10: Show inline retry button for recoverable LLM errors
            if (d.recoverable && d.pipeline === 'gemini') {
                document.getElementById('gemini-graph').innerHTML =
                    `<div class="placeholder">${d.message}` +
                    `<br><button class="retry-btn" data-action="gem-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
                gemRunState.docId = gemRunState.docId || document.getElementById('doc-id-input').value.toUpperCase();
            }
            if (d.recoverable && d.pipeline === 'modular') {
                document.getElementById('modular-graph').innerHTML =
                    `<div class="placeholder">${d.message}` +
                    `<br><button class="retry-btn" data-action="mod-refresh" style="margin-top:12px;">&#8635; Retry</button></div>`;
                modRunState.docId = modRunState.docId || document.getElementById('doc-id-input').value.toUpperCase();
            }
        } else {
            document.getElementById('status').textContent = 'Connection lost';
            eventSource.close();
        }
    });

    eventSource.addEventListener('done', (e) => {
        const d = JSON.parse(e.data);
        document.getElementById('status').textContent =
            `Complete (${(d.total_duration_ms / 1000).toFixed(1)}s)`;
        eventSource.close();

        // Ensure combined matrix table visible
        document.getElementById('analysis-panel').style.display = 'block';

        // Sprint 33.5: Engine version footer
        if (d.cache_version) {
            document.getElementById('engine-version').textContent = 'Engine Version: ' + d.cache_version;
            document.getElementById('engine-version').style.display = 'block';
        }

        // Sprint 33.5: Highlight best throughput in green
        highlightBestThroughput();
        // Sprint 33.6: Cost comparison row
        highlightBestCost();

        // Auto-trigger analysis if not already received via SSE
        if (!state.compare.analysis && state.compare.stats_kgs) {
            refreshAnalysis();
        }
    });
}

// Sprint 33.5: Highlight best throughput value in green
function highlightBestThroughput() {
    const ids = ['audit-kgen-throughput', 'audit-fullshot-throughput', 'audit-multistage-throughput'];
    let bestVal = 0;
    let bestId = null;
    for (const id of ids) {
        const el = document.getElementById(id);
        const val = parseFloat(el.textContent);
        if (!isNaN(val) && val > bestVal) {
            bestVal = val;
            bestId = id;
        }
        el.style.color = '';  // Reset
    }
    if (bestId) {
        document.getElementById(bestId).style.color = '#5ED68A';
        document.getElementById(bestId).style.fontWeight = 'bold';
    }
}

// Sprint 33.6: Cancel Multi-Stage extraction
function cancelMultistage() {
    if (!modRunState.docId) return;
    fetch(`/api/cancel-multistage/${modRunState.docId}`, { method: 'POST' });
    document.getElementById('mod-cancel').style.display = 'none';
}

// Sprint 33.6: Cost comparison row
function highlightBestCost() {
    const cells = [
        { id: 'audit-kgen-cost', multId: 'audit-kgen-costmult' },
        { id: 'audit-fullshot-cost', multId: 'audit-fullshot-costmult' },
        { id: 'audit-multistage-cost', multId: 'audit-multistage-costmult' },
    ];
    const costs = cells.map(c => {
        const el = document.getElementById(c.id);
        // Use data attribute for raw cost value (set by fmtCostCell)
        const raw = el.dataset.rawCost;
        return raw ? parseFloat(raw) : null;
    });
    const validCosts = costs.filter(c => c !== null && c > 0);
    if (validCosts.length < 2) return;
    const minCost = Math.min(...validCosts);
    cells.forEach((c, i) => {
        const el = document.getElementById(c.multId);
        if (costs[i] !== null && costs[i] > 0) {
            const mult = costs[i] / minCost;
            if (mult <= 1.01) {
                el.textContent = '1x (Best)';
                el.style.color = '#5ED68A';
                el.style.fontWeight = 'bold';
            } else {
                el.textContent = mult.toFixed(1) + 'x';
                el.style.color = '#FF6B6B';
                el.style.fontWeight = '';
            }
        }
    });
}

function resetCompareUI() {
    const tl = tabTimeline.compare;
    document.getElementById('compare-timeline-steps').innerHTML = '';
    document.getElementById('kgenskills-graph').innerHTML = '<div class="placeholder">Waiting for extraction...</div>';
    document.getElementById('modular-graph').innerHTML = '<div class="placeholder">Waiting for extraction...</div>';
    document.getElementById('gemini-graph').innerHTML = '<div class="placeholder">Waiting for extraction...</div>';
    document.getElementById('kgenskills-stats').innerHTML = '';
    document.getElementById('modular-stats').innerHTML = '';
    document.getElementById('gemini-stats').innerHTML = '';
    document.getElementById('kgenskills-legend').innerHTML = '';
    document.getElementById('modular-legend').innerHTML = '';
    document.getElementById('gemini-legend').innerHTML = '';
    document.getElementById('kgenskills-rel-legend').innerHTML = '';
    document.getElementById('modular-rel-legend').innerHTML = '';
    document.getElementById('gemini-rel-legend').innerHTML = '';
    document.getElementById('kgenskills-toolbar').style.display = 'none';
    document.getElementById('modular-toolbar').style.display = 'none';
    document.getElementById('gemini-toolbar').style.display = 'none';
    document.getElementById('analysis-panel').style.display = 'none';
    document.getElementById('analysis-action').style.display = 'none';
    document.getElementById('qualitative-analysis').style.display = 'none';
    document.getElementById('analysis-content').innerHTML = '';
    document.getElementById('diagnostic-scores').style.display = 'none';
    document.getElementById('efficiency-audit').style.display = 'none';
    document.getElementById('compare-source-panel').style.display = 'none';
    document.getElementById('kgenskills-progress').style.display = 'none';
    document.getElementById('kgenskills-progress-bar').style.width = '0%';
    document.getElementById('modular-progress').style.display = 'none';
    document.getElementById('modular-progress-bar').style.width = '0%';
    document.getElementById('gemini-progress').style.display = 'none';
    document.getElementById('gemini-progress-bar').style.width = '0%';
    document.getElementById('kgen-history').style.display = 'none';
    document.getElementById('modular-history').style.display = 'none';
    document.getElementById('gemini-history').style.display = 'none';
    document.getElementById('mod-cancel').style.display = 'none';
    tl.stepOrder.length = 0;
    for (const k in tl.stepElements) delete tl.stepElements[k];
    // Clear network refs
    delete networks['kgenskills'];
    delete networks['modular'];
    delete networks['gemini'];
}

// ============================================================
// Source Panel
// ============================================================
function showSourcePanel(details) {
    const panel = document.getElementById('compare-source-panel');
    const doc = document.getElementById('compare-source-doc');
    if (!details) return;

    let html = '<span class="source-label">Source:</span> ';
    // Wave A: backend emits `doc_id`; fall back to the pre-Wave-A `ticker`
    // key while older cached fixtures might still carry it.
    const name = details.company_name || details.doc_id || details.ticker || '';
    const date = details.filing_date || '';
    const accession = details.accession_number || '';
    const url = details.source_url || '';
    const sizeKb = details.size_kb || '';

    if (url) {
        html += `<a href="${url}" target="_blank">${name} 10-K${date ? ` (${date})` : ''}</a>`;
    } else {
        html += `<span style="color:#ccc">${name} 10-K${date ? ` (${date})` : ''}</span>`;
    }

    const metaParts = [];
    if (sizeKb) metaParts.push(`${sizeKb}KB`);
    if (accession) metaParts.push(`Accession: ${accession}`);
    if (metaParts.length) {
        html += `<span class="source-meta">${metaParts.join(' · ')}</span>`;
    }

    state.compare.source = details;
    doc.innerHTML = html;
    panel.style.display = 'block';
}

// ============================================================
// Timeline Functions (shared across tabs)
// ============================================================

// --- compare.html lines 6127-6552: matrixBadge + fmt* + rankThree + updateComparisonMatrix + renderAnalysis + renderScores + clearAnalysis ---
function matrixBadge(grade, text) {
    return `<span class="matrix-badge"><span class="matrix-dot ${grade}"></span> ${text}</span>`;
}
function fmtCost(v) {
    if (v === 0) return '$0';
    if (v < 0.0001) return `$${(v * 1_000_000).toFixed(2)}µ`;
    if (v < 0.01) return `$${v.toFixed(6)}`;
    return `$${v.toFixed(4)}`;
}
function fmtThroughput(mbPerSec) {
    if (!isFinite(mbPerSec) || isNaN(mbPerSec)) return '--';
    let rate;
    if (mbPerSec >= 1024) rate = `${(mbPerSec / 1024).toFixed(1)} GB/s`;
    else if (mbPerSec >= 1) rate = `${mbPerSec.toFixed(1)} MB/s`;
    else rate = `${(mbPerSec * 1024).toFixed(0)} KB/s`;
    // Add time to process 1 GB
    const secsPerGB = 1024 / mbPerSec;
    let gbTime;
    if (secsPerGB < 60) gbTime = `${secsPerGB.toFixed(0)}s`;
    else if (secsPerGB < 3600) gbTime = `${(secsPerGB / 60).toFixed(1)}m`;
    else gbTime = `${(secsPerGB / 3600).toFixed(1)}h`;
    return `${rate} (${gbTime}/GB)`;
}

function rankThree(a, b, c, lowerIsBetter) {
    const vals = [a, b, c];
    const sorted = [...vals].sort((x, y) => lowerIsBetter ? x - y : y - x);
    return vals.map(v => v === sorted[0] ? 'best' : v === sorted[2] ? 'poor' : 'good');
}

function updateComparisonMatrix() {
    const kgs = state.compare.stats_kgs;
    const gem = state.compare.stats_gem;
    const mod = state.compare.stats_mod;

    // Static rows (always populated)
    document.getElementById('mx-repro-kgen').innerHTML = matrixBadge('best', '100% Deterministic');
    document.getElementById('mx-repro-gem').innerHTML = matrixBadge('poor', 'Stochastic');
    document.getElementById('mx-repro-mod').innerHTML = matrixBadge('poor', 'Stochastic');

    document.getElementById('mx-prov-kgen').innerHTML = matrixBadge('best', 'Exact Lineage');
    document.getElementById('mx-prov-gem').innerHTML = matrixBadge('poor', 'Hallucination Risk');
    document.getElementById('mx-prov-mod').innerHTML = matrixBadge('good', 'Moderate Traceability');

    document.getElementById('mx-schema-kgen').innerHTML = matrixBadge('good', 'YAML Compilation');
    document.getElementById('mx-schema-gem').innerHTML = matrixBadge('best', 'Natural Language');
    document.getElementById('mx-schema-mod').innerHTML = matrixBadge('poor', 'Complex Prompting');

    // Dynamic rows — show provisional badges as each pipeline completes
    let corpusKb = parseInt(document.getElementById('corpus-kb-select').value);
    // Full Document mode: use actual processed size from pipeline stats
    if (!corpusKb) {
        corpusKb = (kgs && kgs.actual_kb) || (gem && gem.actual_kb) || (mod && mod.actual_kb) || 200;
    }
    const gbScale = 1_048_576 / corpusKb;

    if (kgs) {
        const cpg = (kgs.cpu_cost || 0) * gbScale;
        document.getElementById('mx-cost-kgen').innerHTML = matrixBadge('best', `${fmtCost(cpg)}/GB`);
        if (kgs.relationships > 0) {
            const cpr = (kgs.cpu_cost || 0) / kgs.relationships;
            document.getElementById('mx-costrel-kgen').innerHTML = matrixBadge('best', `${fmtCost(cpr)}/rel`);
        }
        document.getElementById('mx-speed-kgen').innerHTML = matrixBadge('best', `${(kgs.duration_ms / 1000).toFixed(1)}s`);
        if (kgs.duration_ms > 0) {
            const chunks = kgs.num_chunks || 1;
            const mbps = chunks * (corpusKb / 1024) / (kgs.duration_ms / 1000);
            document.getElementById('mx-parlat-kgen').innerHTML = matrixBadge('best',
                `${fmtThroughput(mbps)}${kgs.num_chunks > 0 ? ` (${kgs.num_chunks} CPUs)` : ''}`);
        }
    }
    if (gem) {
        const cpg = calcCost(gem.tokens || 0) * gbScale;
        document.getElementById('mx-cost-gem').innerHTML = matrixBadge('poor', `${fmtCost(cpg)}/GB`);
        if (gem.relationships > 0) {
            const cpr = calcCost(gem.tokens || 0) / gem.relationships;
            document.getElementById('mx-costrel-gem').innerHTML = matrixBadge('poor', `${fmtCost(cpr)}/rel`);
        }
        document.getElementById('mx-speed-gem').innerHTML = matrixBadge('good', `${(gem.duration_ms / 1000).toFixed(1)}s`);
        if (gem.duration_ms > 0) {
            const mbps = (corpusKb / 1024) / (gem.duration_ms / 1000);
            document.getElementById('mx-parlat-gem').innerHTML = matrixBadge('poor', `${fmtThroughput(mbps)} (1 call)`);
        }
    }
    if (mod) {
        const cpg = calcCost(mod.tokens || 0) * gbScale;
        document.getElementById('mx-cost-mod').innerHTML = matrixBadge('good', `${fmtCost(cpg)}/GB`);
        if (mod.relationships > 0) {
            const cpr = calcCost(mod.tokens || 0) / mod.relationships;
            document.getElementById('mx-costrel-mod').innerHTML = matrixBadge('good', `${fmtCost(cpr)}/rel`);
        }
        document.getElementById('mx-speed-mod').innerHTML = matrixBadge('poor', `${(mod.duration_ms / 1000).toFixed(1)}s`);
        if (mod.duration_ms > 0) {
            const chunks = mod.chunks_total || 1;
            const mbps = chunks * (corpusKb / 1024) / (mod.duration_ms / 1000);
            document.getElementById('mx-parlat-mod').innerHTML = matrixBadge('good',
                `${fmtThroughput(mbps)}${mod.chunks_total > 0 ? ` (${mod.chunks_total} threads)` : ''}`);
        }
    }

    // Static base scores: repro(3+1+1) + prov(3+1+2) + schema(2+3+1)
    const scores = { kgen: 8, gem: 5, mod: 4 };

    // Final grades + totals when all 3 are ready
    if (kgs && gem && mod) {
        const gradeToPoints = { best: 3, good: 2, poor: 1 };

        const kgenCpg = (kgs.cpu_cost || 0) * gbScale;
        const gemCpg = calcCost(gem.tokens || 0) * gbScale;
        const modCpg = calcCost(mod.tokens || 0) * gbScale;
        const [cg1, cg2, cg3] = rankThree(kgenCpg, gemCpg, modCpg, true);
        document.getElementById('mx-cost-kgen').innerHTML = matrixBadge(cg1, `${fmtCost(kgenCpg)}/GB`);
        document.getElementById('mx-cost-gem').innerHTML = matrixBadge(cg2, `${fmtCost(gemCpg)}/GB`);
        document.getElementById('mx-cost-mod').innerHTML = matrixBadge(cg3, `${fmtCost(modCpg)}/GB`);
        scores.kgen += gradeToPoints[cg1];
        scores.gem += gradeToPoints[cg2];
        scores.mod += gradeToPoints[cg3];

        // Cost/Relation (lower is better)
        const kgenCpr = kgs.relationships > 0 ? (kgs.cpu_cost || 0) / kgs.relationships : 999;
        const gemCpr = gem.relationships > 0 ? calcCost(gem.tokens || 0) / gem.relationships : 999;
        const modCpr = mod.relationships > 0 ? calcCost(mod.tokens || 0) / mod.relationships : 999;
        const [cr1, cr2, cr3] = rankThree(kgenCpr, gemCpr, modCpr, true);
        document.getElementById('mx-costrel-kgen').innerHTML = matrixBadge(cr1, kgs.relationships > 0 ? `${fmtCost(kgenCpr)}/rel` : '--');
        document.getElementById('mx-costrel-gem').innerHTML = matrixBadge(cr2, gem.relationships > 0 ? `${fmtCost(gemCpr)}/rel` : '--');
        document.getElementById('mx-costrel-mod').innerHTML = matrixBadge(cr3, mod.relationships > 0 ? `${fmtCost(modCpr)}/rel` : '--');
        scores.kgen += gradeToPoints[cr1];
        scores.gem += gradeToPoints[cr2];
        scores.mod += gradeToPoints[cr3];

        const [sg1, sg2, sg3] = rankThree(kgs.duration_ms, gem.duration_ms, mod.duration_ms, true);
        document.getElementById('mx-speed-kgen').innerHTML = matrixBadge(sg1, `${(kgs.duration_ms / 1000).toFixed(1)}s`);
        document.getElementById('mx-speed-gem').innerHTML = matrixBadge(sg2, `${(gem.duration_ms / 1000).toFixed(1)}s`);
        document.getElementById('mx-speed-mod').innerHTML = matrixBadge(sg3, `${(mod.duration_ms / 1000).toFixed(1)}s`);
        scores.kgen += gradeToPoints[sg1];
        scores.gem += gradeToPoints[sg2];
        scores.mod += gradeToPoints[sg3];

        // Projected throughput (parallel scaling, MB/sec — higher is better)
        const corpusMb = corpusKb / 1024;
        const kgenMbps = (kgs.num_chunks > 0) ? kgs.num_chunks * corpusMb / (kgs.duration_ms / 1000) : corpusMb / (kgs.duration_ms / 1000);
        const gemMbps = corpusMb / (gem.duration_ms / 1000);
        const modMbps = (mod.chunks_total > 0) ? mod.chunks_total * corpusMb / (mod.duration_ms / 1000) : corpusMb / (mod.duration_ms / 1000);
        const [pg1, pg2, pg3] = rankThree(kgenMbps, gemMbps, modMbps, false);
        document.getElementById('mx-parlat-kgen').innerHTML = matrixBadge(pg1,
            kgs.num_chunks > 0 ? `${fmtThroughput(kgenMbps)} (${kgs.num_chunks} CPUs)` : fmtThroughput(kgenMbps));
        document.getElementById('mx-parlat-gem').innerHTML = matrixBadge(pg2, `${fmtThroughput(gemMbps)} (1 call)`);
        document.getElementById('mx-parlat-mod').innerHTML = matrixBadge(pg3,
            mod.chunks_total > 0 ? `${fmtThroughput(modMbps)} (${mod.chunks_total} threads)` : fmtThroughput(modMbps));
        scores.kgen += gradeToPoints[pg1];
        scores.gem += gradeToPoints[pg2];
        scores.mod += gradeToPoints[pg3];

        const maxScore = Math.max(scores.kgen, scores.gem, scores.mod);
        const crown = ' &#x1F451;';
        document.getElementById('mx-total-kgen').innerHTML =
            `<span class="${scores.kgen === maxScore ? 'matrix-winner' : ''}">${scores.kgen}${scores.kgen === maxScore ? crown : ''}</span>`;
        document.getElementById('mx-total-gem').innerHTML =
            `<span class="${scores.gem === maxScore ? 'matrix-winner' : ''}">${scores.gem}${scores.gem === maxScore ? crown : ''}</span>`;
        document.getElementById('mx-total-mod').innerHTML =
            `<span class="${scores.mod === maxScore ? 'matrix-winner' : ''}">${scores.mod}${scores.mod === maxScore ? crown : ''}</span>`;
    }

    document.getElementById('analysis-panel').style.display = 'block';
    // Always show the Analyze button when matrix is visible
    document.getElementById('analysis-action').style.display = 'block';
    if (state.compare.analysis) {
        document.getElementById('qualitative-analysis').style.display = 'block';
    }
}

function renderAnalysis(a) {
    if (!a) return;
    const el = document.getElementById('analysis-content');
    const winner = a.winner || 'tie';
    const winnerLabel = {kgenskills:'KGSpin', fullshot:'LLM Full Shot', multistage:'LLM Multi-Stage', tie:'Tie'}[winner] || winner;

    // Sprint 90: Per-pipeline cards with schema compliance
    const pipelines = a.pipelines || {};
    const compliance = a.schema_compliance || {};
    const pipLabels = {kgenskills: 'KGSpin', fullshot: 'LLM Full Shot', multistage: 'LLM Multi-Stage'};
    const pipColors = {kgenskills: '#5ED68A', fullshot: '#E74C3C', multistage: '#F39C12'};

    let pipelineCards = '';
    for (const pip of ['kgenskills', 'fullshot', 'multistage']) {
        const pData = pipelines[pip];
        if (!pData) continue;
        const comp = compliance[pip];
        const compBadge = comp
            ? `<div style="margin:6px 0; padding:4px 8px; background:#1a2a3a; border-radius:4px; font-size:12px;">
                 <span style="color:#B0B0B0;">Schema Compliance:</span>
                 <span style="color:${comp.compliance_pct >= 90 ? '#5ED68A' : comp.compliance_pct >= 70 ? '#FFE066' : '#ff6b6b'}; font-weight:700;">${comp.compliance_pct}%</span>
                 <span style="color:#888;">(${comp.on_schema}/${comp.total})</span>
                 ${comp.off_schema_types && comp.off_schema_types.length > 0 ? `<div style="color:#ff6b6b; font-size:11px; margin-top:2px;">Off-schema: ${comp.off_schema_types.join(', ')}</div>` : ''}
               </div>`
            : '';

        // Fall back to old scores format if pipelines data missing precision/recall
        const prec = pData.precision || '--';
        const rec = pData.recall || '--';

        pipelineCards += `
            <div class="analysis-card" style="border-left: 3px solid ${pipColors[pip] || '#888'};">
                <h3 style="color:${pipColors[pip] || '#fff'}">${pipLabels[pip] || pip}</h3>
                ${compBadge}
                <div class="detail" style="margin:6px 0;">${pData.assessment || ''}</div>
                <div class="detail"><b>Strengths:</b> ${pData.strengths || '--'}</div>
                <div class="detail"><b>Weaknesses:</b> ${pData.weaknesses || '--'}</div>
                <div style="display:flex; gap:12px; margin-top:8px;">
                    <div class="detail"><b>Precision:</b> ${prec}</div>
                    <div class="detail"><b>Recall:</b> ${rec}</div>
                </div>
            </div>`;
    }

    // Fallback: if no pipelines object (old format), use legacy scores
    if (!pipelineCards) {
        const scores = a.scores || {};
        for (const pip of ['kgenskills', 'fullshot', 'multistage']) {
            const prec = scores[`${pip}_precision`];
            const rec = scores[`${pip}_recall`];
            if (!prec && !rec) continue;
            pipelineCards += `
                <div class="analysis-card">
                    <h3>${pipLabels[pip] || pip}</h3>
                    <div class="detail"><b>Precision:</b> ${prec || '--'}</div>
                    <div class="detail"><b>Recall:</b> ${rec || '--'}</div>
                </div>`;
        }
    }

    el.innerHTML = `
        <div class="analysis-summary">${a.summary || ''}</div>
        <div class="analysis-grid">
            ${pipelineCards}
            <div class="analysis-card" style="border-left: 3px solid #5B9FE6;">
                <h3>Winner</h3>
                <div class="value" style="color:#5ED68A; font-size:18px;">${winnerLabel}</div>
                <div class="detail">${a.winner_reason || ''}</div>
            </div>
            <div class="analysis-card" style="border-left: 3px solid #E088E5;">
                <h3>Cost &amp; Efficiency</h3>
                <div class="detail">${a.cost_analysis || ''}</div>
            </div>
        </div>`;
    document.getElementById('qualitative-analysis').style.display = 'block';
}

function renderScores(d) {
    const panel = document.getElementById('diagnostic-scores');
    panel.style.display = 'block';
    const container = document.getElementById('pairwise-matrix');
    const pairs = d.pairs || {};

    // Build pipeline list from available pairs
    const pipelineSet = new Set();
    const pipelineMap = {
        'kgs': {label: 'KGSpin', color: '#5ED68A'},
        'multistage': {label: 'LLM Multi-Stage', color: '#F39C12'},
        'fullshot': {label: 'LLM Full Shot', color: '#E74C3C'},
    };
    const pairKeyMap = {};
    for (const key of Object.keys(pairs)) {
        const parts = key.split('_vs_');
        pipelineSet.add(parts[0]);
        pipelineSet.add(parts[1]);
        pairKeyMap[parts[0] + '_' + parts[1]] = pairs[key];
        pairKeyMap[parts[1] + '_' + parts[0]] = pairs[key]; // symmetric lookup
    }
    const pipelines = ['kgs', 'multistage', 'fullshot'].filter(p => pipelineSet.has(p));
    if (pipelines.length < 2) {
        container.innerHTML = '<div style="color:#888">Need at least 2 pipelines for comparison</div>';
        return;
    }

    // Color scale: 0% = dark red, 50% = dark yellow, 100% = dark green
    function heatColor(pct) {
        if (pct <= 50) {
            const r = Math.round(100 + (pct / 50) * 55);
            const g = Math.round(30 + (pct / 50) * 70);
            return `rgb(${r}, ${g}, 30)`;
        } else {
            const r = Math.round(155 - ((pct - 50) / 50) * 115);
            const g = Math.round(100 + ((pct - 50) / 50) * 80);
            return `rgb(${r}, ${g}, 40)`;
        }
    }

    function getPairData(a, b) {
        return pairKeyMap[a + '_' + b] || null;
    }

    // Entity heatmap
    let html = '<div style="margin-bottom:16px;">';
    html += '<div style="font-weight:600; color:#B0B0B0; font-size:12px; margin-bottom:6px;">ENTITY OVERLAP</div>';
    html += '<table style="width:100%; border-collapse:collapse; font-size:12px;">';
    // Header
    html += '<tr><td style="width:90px;"></td>';
    for (const p of pipelines) {
        html += `<td style="text-align:center; padding:4px 6px; color:${pipelineMap[p].color}; font-weight:600; font-size:11px;">${pipelineMap[p].label}</td>`;
    }
    html += '</tr>';
    // Rows
    for (const row of pipelines) {
        html += `<tr><td style="padding:4px 6px; color:${pipelineMap[row].color}; font-weight:600; font-size:11px; white-space:nowrap;">${pipelineMap[row].label}</td>`;
        for (const col of pipelines) {
            if (row === col) {
                // Diagonal: show entity count
                let count = 0;
                for (const pair of Object.values(pairs)) {
                    if (pair.a_entities && pairKeyMap[row + '_' + Object.keys(pairs)[0]?.split('_vs_')[1]]) {
                        // Find this pipeline's entity count from any pair
                        break;
                    }
                }
                // Get count from first pair containing this pipeline
                for (const [key, data] of Object.entries(pairs)) {
                    const parts = key.split('_vs_');
                    if (parts[0] === row) { count = data.a_entities; break; }
                    if (parts[1] === row) { count = data.b_entities; break; }
                }
                html += `<td style="text-align:center; padding:6px; background:#1a2a3a; color:#9B9BFF; font-weight:700; border-radius:4px;">${count}</td>`;
            } else {
                const data = getPairData(row, col);
                if (data) {
                    const total = data.entity_overlap + data.a_only_entities + data.b_only_entities;
                    const pct = total > 0 ? Math.round((data.entity_overlap / total) * 100) : 0;
                    const bg = heatColor(pct);
                    html += `<td style="text-align:center; padding:6px; background:${bg}; color:#fff; font-weight:600; border-radius:4px; cursor:help;" title="${data.entity_overlap} shared / ${total} union">${pct}%</td>`;
                } else {
                    html += '<td style="text-align:center; padding:6px; color:#555;">--</td>';
                }
            }
        }
        html += '</tr>';
    }
    html += '</table></div>';

    // Relationship heatmap
    html += '<div>';
    html += '<div style="font-weight:600; color:#B0B0B0; font-size:12px; margin-bottom:6px;">RELATIONSHIP OVERLAP</div>';
    html += '<table style="width:100%; border-collapse:collapse; font-size:12px;">';
    html += '<tr><td style="width:90px;"></td>';
    for (const p of pipelines) {
        html += `<td style="text-align:center; padding:4px 6px; color:${pipelineMap[p].color}; font-weight:600; font-size:11px;">${pipelineMap[p].label}</td>`;
    }
    html += '</tr>';
    for (const row of pipelines) {
        html += `<tr><td style="padding:4px 6px; color:${pipelineMap[row].color}; font-weight:600; font-size:11px; white-space:nowrap;">${pipelineMap[row].label}</td>`;
        for (const col of pipelines) {
            if (row === col) {
                let count = 0;
                for (const [key, data] of Object.entries(pairs)) {
                    const parts = key.split('_vs_');
                    if (parts[0] === row) { count = data.a_relationships; break; }
                    if (parts[1] === row) { count = data.b_relationships; break; }
                }
                html += `<td style="text-align:center; padding:6px; background:#1a2a3a; color:#9B9BFF; font-weight:700; border-radius:4px;">${count}</td>`;
            } else {
                const data = getPairData(row, col);
                if (data) {
                    const total = data.relationship_overlap + data.a_only_relationships + data.b_only_relationships;
                    const pct = total > 0 ? Math.round((data.relationship_overlap / total) * 100) : 0;
                    const bg = heatColor(pct);
                    html += `<td style="text-align:center; padding:6px; background:${bg}; color:#fff; font-weight:600; border-radius:4px; cursor:help;" title="${data.relationship_overlap} shared / ${total} union">${pct}%</td>`;
                } else {
                    html += '<td style="text-align:center; padding:6px; color:#555;">--</td>';
                }
            }
        }
        html += '</tr>';
    }
    html += '</table></div>';

    // Legend
    html += '<div style="margin-top:8px; font-size:10px; color:#777; text-align:center;">Diagonal = total count &bull; Off-diagonal = Jaccard overlap % (shared / union) &bull; Hover for details</div>';

    container.innerHTML = html;
}

async function refreshScores() {
    const ticker = state.compare.source?.doc_id || state.docId;
    if (!ticker) return;
    try {
        const resp = await fetch(`/api/scores/${ticker}`);
        if (!resp.ok) return;
        renderScores(await resp.json());
    } catch (e) {
        console.warn('Failed to refresh scores:', e);
    }
}

function clearAnalysis() {
    state.compare.analysis = null;
    document.getElementById('qualitative-analysis').style.display = 'none';
    document.getElementById('analysis-content').innerHTML = '';
    // Keep the Analyze button visible so user can re-trigger
    if (document.getElementById('analysis-panel').style.display === 'block') {
        document.getElementById('analysis-action').style.display = 'block';
    }
}

async function refreshAnalysis() {
    const ticker = state.compare.source?.doc_id || state.docId;
    if (!ticker) return;
    document.getElementById('analysis-content').innerHTML = '<div style="color:#888">Analyzing...</div>';
    document.getElementById('qualitative-analysis').style.display = 'block';
    try {
        const resp = await fetch(`/api/refresh-analysis/${ticker}`, { method: 'POST' });
        const data = await resp.json();
        if (data.error) {
            document.getElementById('analysis-content').innerHTML = `<div style="color:#FF6B6B">${data.error}</div>`;
            return;
        }
        state.compare.analysis = data.analysis;
        renderAnalysis(data.analysis);
        if (data.scores) renderScores(data.scores);
        updateComparisonMatrix();
    } catch (e) {
        document.getElementById('analysis-content').innerHTML = `<div style="color:#FF6B6B">Analysis failed: ${e.message}</div>`;
    }
}

// ============================================================
// Sprint 79: Document metadata display
// ============================================================

// --- compare.html lines 7596-7612: refreshScores ---
function refreshScores() {
    const ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    if (!ticker) return;
    fetch(`/api/scores/${ticker}`)
        .then(r => r.json())
        .then(data => renderScores(data))
        .catch(err => console.warn('Score refresh failed:', err));
}

// ============================================================
// Sprint 91: Slot-Based Pipeline Management
// ============================================================

// INIT-001 Sprint 04: PIPELINE_META keys are now the canonical slot IDs from
// ui_slots.yaml. Strategy values match kgspin-core's _VALID_STRATEGIES set
// (post-INIT-006 Sprint 02 hard-break rename). See
// docs/architecture/product_naming_proposal.md for the rename table.

// --- compare.html lines 8031-8043: getPopulatedSlotCount + getSlotDescriptors ---
function getPopulatedSlotCount() {
    return slotState.filter(s => s.visData !== null).length;
}

function getSlotDescriptors() {
    return slotState
        .filter(s => s.visData !== null)
        .map(s => `${s.pipeline}:${s.bundle || 'default'}:${s.strategy || ''}`)
        .sort();
}

// Sprint 100: "Why This Matters" hero section
// Sprint 155: Default questions per domain — aligned with bundle predicates

// --- compare.html lines 8105-8122: updateAnalyzeButton ---
function updateAnalyzeButton() {
    const btn = document.getElementById('analyze-btn');
    const qaBtn = document.getElementById('qa-btn');
    const count = getPopulatedSlotCount();
    if (btn) {
        btn.disabled = count < 2;
        btn.title = count < 2 ? 'Load at least 2 graphs to analyze' : 'Run qualitative analysis';
    }
    if (qaBtn) {
        qaBtn.disabled = count < 2;
        qaBtn.title = count < 2 ? 'Load at least 2 graphs to run Q&A' : 'Run side-by-side Q&A';
    }
}

// ============================================================
// Sprint 91: Per-Slot Run History Navigation
// ============================================================


// --- compare.html lines 8950-8965: GEMINI_COST_* + SLOT_TO_REPRO_KEY ---
const GEMINI_COST_PER_1M_INPUT = 0.10;
const GEMINI_COST_PER_1M_OUTPUT = 0.40;
const GEMINI_BLENDED_COST_PER_TOKEN = (GEMINI_COST_PER_1M_INPUT + GEMINI_COST_PER_1M_OUTPUT) / 2 / 1_000_000;

const analysisCache = {};

// Map slot pipeline keys to reproducibility API field names.
// INIT-001 Sprint 04: keys are canonical slot IDs from ui_slots.yaml.
// Internal cache bucket names ('kgen', 'fullshot', 'modular') are unchanged
// to avoid churning on-disk run logs.
const SLOT_TO_REPRO_KEY = {
    'discovery_rapid': 'kgen', 'discovery_deep': 'kgen', 'fan_out': 'kgen',
    'agentic_flash': 'fullshot', 'agentic_analyst': 'modular',
};

// Heatmap color scale: 0%=dark red, 50%=dark yellow, 100%=dark green

// --- compare.html lines 8966-9573: heatColor + slot analysis + Q&A renderers ---
function heatColor(pct) {
    if (pct <= 50) {
        const r = Math.round(100 + (pct / 50) * 55);
        const g = Math.round(30 + (pct / 50) * 70);
        return `rgb(${r}, ${g}, 30)`;
    } else {
        const r = Math.round(155 - ((pct - 50) / 50) * 115);
        const g = Math.round(100 + ((pct - 50) / 50) * 80);
        return `rgb(${r}, ${g}, 40)`;
    }
}

// Rank N values: returns grades array ['best','good','poor'] based on rank
function rankN(values, lowerIsBetter) {
    if (values.length <= 1) return values.map(() => 'best');
    const sorted = [...values].filter(v => v != null && !isNaN(v)).sort((a, b) => lowerIsBetter ? a - b : b - a);
    return values.map(v => {
        if (v == null || isNaN(v)) return 'poor';
        if (v === sorted[0]) return 'best';
        if (v === sorted[sorted.length - 1]) return 'poor';
        return 'good';
    });
}

// Get slot label for display
function slotLabel(slot) {
    const meta = PIPELINE_META[slot.pipeline];
    if (!meta) return 'Unknown';
    const bundle = slot.bundle ? ` (${slot.bundle})` : '';
    return `${meta.label}${bundle}`;
}

// Compute cost from tokens (KGSpin = $0 always)
function slotCost(slot) {
    const meta = PIPELINE_META[slot.pipeline];
    if (!meta) return 0;
    if (meta.isKgspin) {
        // CPU time × hourly rate
        return (slot.stats && slot.stats.cpu_cost) || 0;
    }
    // Total tokens × cost/token (80/20 input/output split)
    const tokens = (slot.stats && slot.stats.tokens) || 0;
    return calcCost(tokens);
}

// ---------- Renderer: Combined Comparison Matrix ----------
function renderSlotComparisonMatrix(slots) {
    const n = slots.length;
    let html = '<div style="margin-bottom:20px;">';
    html += '<h4 style="color:#5B9FE6; margin:0 0 10px;">Comparison Matrix</h4>';
    html += '<table style="width:100%; border-collapse:collapse; font-size:12px;">';
    // Header
    html += '<thead><tr style="border-bottom:2px solid #2a2a4e;">';
    html += '<th style="text-align:left;padding:6px 8px;color:#888;width:140px;"></th>';
    slots.forEach(s => {
        const meta = PIPELINE_META[s.pipeline];
        html += `<th style="text-align:center;padding:6px 8px;color:${meta.color};font-size:11px;">${slotLabel(s)}</th>`;
    });
    html += '</tr></thead><tbody>';

    // Scoring accumulator
    const totals = slots.map(() => 0);

    // Helper: add a ranked row with matrixBadge
    function addDynRow(label, numericValues, displayTexts, lowerIsBetter) {
        const grades = rankN(numericValues, lowerIsBetter);
        html += `<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:5px 8px;color:#aaa;">${label}</td>`;
        displayTexts.forEach((display, i) => {
            html += `<td style="text-align:center;padding:5px 8px;">${matrixBadge(grades[i], display || '--')}</td>`;
            totals[i] += grades[i] === 'best' ? 3 : grades[i] === 'good' ? 2 : 1;
        });
        html += '</tr>';
    }

    // Helper: add a static row
    function addStaticRow(label, slotMapper) {
        html += `<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:5px 8px;color:#aaa;">${label}</td>`;
        slots.forEach((s, i) => {
            const { grade, text } = slotMapper(s);
            html += `<td style="text-align:center;padding:5px 8px;">${matrixBadge(grade, text)}</td>`;
            totals[i] += grade === 'best' ? 3 : grade === 'good' ? 2 : 1;
        });
        html += '</tr>';
    }

    // Helper: add a plain info row (no badge, no scoring)
    function addInfoRow(label, displayTexts) {
        html += `<tr style="border-bottom:1px solid #1a1a2e;"><td style="padding:5px 8px;color:#aaa;">${label}</td>`;
        displayTexts.forEach(display => {
            html += `<td style="text-align:center;padding:5px 8px;color:#ccc;">${display}</td>`;
        });
        html += '</tr>';
    }

    // --- Tokens ---
    addInfoRow('Tokens', slots.map(s => {
        const meta = PIPELINE_META[s.pipeline];
        const tokens = (s.stats && s.stats.tokens) || 0;
        return meta.isKgspin ? '<span style="color:#5ED68A;font-weight:700;">0</span>' : tokens.toLocaleString();
    }));

    // --- Est. Cost (total tokens × cost/token, or speed × unit cost) ---
    const costs = slots.map(s => slotCost(s));
    addInfoRow('Est. Cost', slots.map((s, i) => {
        const meta = PIPELINE_META[s.pipeline];
        const c = costs[i];
        if (c <= 0) return '<span style="color:#888">--</span>';
        const annotation = meta.isKgspin ? '$0.05/hr CPU' : costRateLabel();
        return `${fmtCost(c)} <small style="color:#666">@ ${annotation}</small>`;
    }));

    // --- Cost/GB ---
    let corpusKb = 0;
    slots.forEach(s => { if (s.stats && s.stats.actual_kb && !corpusKb) corpusKb = s.stats.actual_kb; });
    if (!corpusKb) corpusKb = 200;
    const gbScale = 1_048_576 / corpusKb;
    const costGbNumeric = costs.map(c => c * gbScale);
    const costGbDisplay = costGbNumeric.map(v => v > 0 ? `${fmtCost(v)}/GB` : '--');
    addDynRow('Cost/GB', costGbNumeric, costGbDisplay, true);

    // --- Cost/Relation ---
    const costPerRelNumeric = slots.map((s, i) => {
        const rels = s.visData ? (s.visData.edges ? s.visData.edges.length : 0) : 0;
        if (rels > 0 && costs[i] > 0) return costs[i] / rels;
        return costs[i] > 0 ? Infinity : 0;
    });
    const costPerRelDisplay = slots.map((s, i) => {
        const rels = s.visData ? (s.visData.edges ? s.visData.edges.length : 0) : 0;
        if (rels > 0 && costs[i] > 0) return fmtCost(costs[i] / rels) + '/rel';
        return '--';
    });
    addDynRow('Cost/Rel', costPerRelNumeric, costPerRelDisplay, true);

    // --- Cost vs Best ---
    const validCosts = costs.filter(c => c > 0);
    const minCost = validCosts.length > 0 ? Math.min(...validCosts) : 0;
    addInfoRow('Cost vs Best', costs.map(c => {
        if (c > 0 && minCost > 0) {
            const mult = c / minCost;
            if (mult <= 1.01) return '<span style="color:#5ED68A;font-weight:700;">1x (Best)</span>';
            return `<span style="color:#FF6B6B;">${mult.toFixed(1)}x</span>`;
        }
        return '<span style="color:#888">--</span>';
    }));

    // --- Speed ---
    const durations = slots.map(s => (s.stats && s.stats.duration_ms) || 0);
    const durDisplay = durations.map(d => d > 0 ? `${(d / 1000).toFixed(1)}s` : '--');
    const durNumeric = durations.map(d => d > 0 ? d : Infinity);
    addDynRow('Speed', durNumeric, durDisplay, true);

    // --- Throughput (file size / time) ---
    const throughputs = slots.map(s => {
        const dur = (s.stats && s.stats.duration_ms) || 0;
        const kb = (s.stats && s.stats.actual_kb) || 0;
        // Primary: actual_kb / seconds. Fallback: throughput_kb_sec from backend.
        if (dur > 0 && kb > 0) return kb / (dur / 1000);
        if (s.stats && s.stats.throughput_kb_sec) return s.stats.throughput_kb_sec;
        return null;
    });
    const tpNumeric = throughputs.map(t => t != null ? t : 0);
    const tpDisplay = throughputs.map(t => t != null ? fmtThroughput(t / 1024) : '--');
    addDynRow('Throughput', tpNumeric, tpDisplay, false);

    // --- Projected Throughput (parallel scaling by strategy/chunks) ---
    const projTp = slots.map((s, i) => {
        const tp = throughputs[i];
        if (tp == null) return null;
        const meta = PIPELINE_META[s.pipeline];
        const mbps = tp / 1024;  // KB/s → MB/s
        if (meta.isKgspin) {
            const chunks = (s.stats && s.stats.num_chunks) || 1;
            return { mbps: mbps * chunks, label: `${fmtThroughput(mbps * chunks)} (${chunks} chunks)` };
        }
        if (s.pipeline === 'agentic_analyst') {
            const chunks = (s.stats && s.stats.chunks_total) || 1;
            return { mbps: mbps * chunks, label: `${fmtThroughput(mbps * chunks)} (${chunks} chunks)` };
        }
        return { mbps: mbps, label: `${fmtThroughput(mbps)} (1 call)` };
    });
    const projNumeric = projTp.map(p => p ? p.mbps : 0);
    const projDisplay = projTp.map(p => p ? p.label : '--');
    addDynRow('Projected Throughput', projNumeric, projDisplay, false);

    // --- Cloud Errors ---
    addInfoRow('Cloud Errors', slots.map(s => {
        const errors = (s.stats && s.stats.errors) || 0;
        return errors > 0 ? `<span style="color:#FF6B6B;font-weight:700;">${errors}</span>` : '0';
    }));

    // Static rows
    addStaticRow('Reproducibility', s => {
        const meta = PIPELINE_META[s.pipeline];
        if (meta.isKgspin) return { grade: 'best', text: '100% Deterministic' };
        return { grade: 'poor', text: 'Stochastic' };
    });

    addStaticRow('Provenance', s => {
        const meta = PIPELINE_META[s.pipeline];
        if (meta.isKgspin) return { grade: 'best', text: 'Exact Lineage' };
        if (s.pipeline === 'agentic_flash') return { grade: 'poor', text: 'Hallucination Risk' };
        return { grade: 'good', text: 'Moderate Traceability' };
    });

    addStaticRow('Schema Setup', s => {
        const meta = PIPELINE_META[s.pipeline];
        if (meta.isKgspin) return { grade: 'good', text: 'YAML Compilation' };
        if (s.pipeline === 'agentic_flash') return { grade: 'best', text: 'Natural Language' };
        return { grade: 'poor', text: 'Complex Prompting' };
    });

    // Total row with winner crown
    const maxTotal = Math.max(...totals);
    html += '<tr style="border-top:2px solid #2a2a4e;"><td style="padding:6px 8px;color:#ccc;font-weight:700;">Total</td>';
    slots.forEach((s, i) => {
        const crown = totals[i] === maxTotal ? ' &#128081;' : '';
        const style = totals[i] === maxTotal ? 'color:#5ED68A;font-weight:700;' : 'color:#ccc;';
        html += `<td style="text-align:center;padding:6px 8px;${style}">${totals[i]}${crown}</td>`;
    });
    html += '</tr>';

    html += '</tbody></table></div>';
    return html;
}

// Legacy alias — efficiency audit is now part of the combined matrix
function renderSlotEfficiencyAudit(slots) { return ''; }

// ---------- Renderer: Pairwise Heatmaps ----------
function renderSlotHeatmaps(slots, scoresData) {
    if (!scoresData || !scoresData.pairs) return '';
    const pairs = scoresData.pairs;

    // Build lookup
    const pairLookup = {};
    for (const [key, data] of Object.entries(pairs)) {
        const parts = key.split('_vs_');
        pairLookup[parts[0] + '_' + parts[1]] = data;
        pairLookup[parts[1] + '_' + parts[0]] = data;
    }

    // Map slots to score pipeline keys (INIT-001 Sprint 04 canonical IDs)
    const SLOT_TO_SCORE_KEY = {
        'discovery_rapid': 'kgs', 'discovery_deep': 'kgs', 'fan_out': 'kgs',
        'agentic_flash': 'fullshot', 'agentic_analyst': 'multistage',
    };

    const pipelineKeys = slots.map(s => SLOT_TO_SCORE_KEY[s.pipeline] || s.pipeline);

    function renderHeatmap(title, overlapField, aOnlyField, bOnlyField, countField) {
        let h = `<div style="margin-bottom:12px;">`;
        h += `<div style="font-weight:600; color:#B0B0B0; font-size:12px; margin-bottom:6px;">${title}</div>`;
        h += '<table style="width:100%; border-collapse:collapse; font-size:12px;">';
        // Header
        h += '<tr><td style="width:90px;"></td>';
        slots.forEach(s => {
            const meta = PIPELINE_META[s.pipeline];
            h += `<td style="text-align:center; padding:4px 6px; color:${meta.color}; font-weight:600; font-size:11px;">${meta.label}</td>`;
        });
        h += '</tr>';
        // Rows
        slots.forEach((row, ri) => {
            const meta = PIPELINE_META[row.pipeline];
            h += `<tr><td style="padding:4px 6px; color:${meta.color}; font-weight:600; font-size:11px; white-space:nowrap;">${meta.label}</td>`;
            slots.forEach((col, ci) => {
                if (ri === ci) {
                    // Diagonal: total count
                    let count = 0;
                    for (const data of Object.values(pairs)) {
                        const parts = Object.keys(pairs).find(k => pairs[k] === data).split('_vs_');
                        if (parts[0] === pipelineKeys[ri]) { count = data[`a_${countField}`] || 0; break; }
                        if (parts[1] === pipelineKeys[ri]) { count = data[`b_${countField}`] || 0; break; }
                    }
                    // Fallback: use visData
                    if (count === 0 && row.visData) {
                        count = countField === 'entities' ? (row.visData.nodes?.length || 0) : (row.visData.edges?.length || 0);
                    }
                    h += `<td style="text-align:center; padding:6px; background:#1a2a3a; color:#9B9BFF; font-weight:700; border-radius:4px;">${count}</td>`;
                } else {
                    const lookupKey = pipelineKeys[ri] + '_' + pipelineKeys[ci];
                    const data = pairLookup[lookupKey];
                    if (data) {
                        const overlap = data[overlapField] || 0;
                        const total = overlap + (data[aOnlyField] || 0) + (data[bOnlyField] || 0);
                        const pct = total > 0 ? Math.round((overlap / total) * 100) : 0;
                        const bg = heatColor(pct);
                        h += `<td style="text-align:center; padding:6px; background:${bg}; color:#fff; font-weight:600; border-radius:4px; cursor:help;" title="${overlap} shared / ${total} union">${pct}%</td>`;
                    } else {
                        h += '<td style="text-align:center; padding:6px; color:#555;">--</td>';
                    }
                }
            });
            h += '</tr>';
        });
        h += '</table></div>';
        return h;
    }

    let html = '<div style="margin-bottom:20px;">';
    html += '<h4 style="color:#5B9FE6; margin:0 0 10px;">Pairwise Performance Delta</h4>';
    html += renderHeatmap('ENTITY OVERLAP', 'entity_overlap', 'a_only_entities', 'b_only_entities', 'entities');
    html += renderHeatmap('RELATIONSHIP OVERLAP', 'relationship_overlap', 'a_only_relationships', 'b_only_relationships', 'relationships');
    html += '<div style="font-size:10px; color:#777; text-align:center;">Diagonal = total count &bull; Off-diagonal = Jaccard overlap % (shared / union) &bull; Hover for details</div>';
    html += '</div>';
    return html;
}

// ---------- Renderer: Variability Scores ----------
function renderSlotVariability(slots, varData) {
    if (!varData || varData.error) return '';

    // Map slot pipelines to reproducibility field names, deduplicate
    const seen = new Set();
    const entries = [];
    slots.forEach(s => {
        const reproKey = SLOT_TO_REPRO_KEY[s.pipeline];
        if (!reproKey || seen.has(reproKey)) return;
        seen.add(reproKey);
        const meta = PIPELINE_META[s.pipeline];
        // The API returns fields: kgen, fullshot, modular (each is an object with variance_pct, num_runs, etc.)
        const data = varData[reproKey];
        if (!data || data.insufficient) return;
        entries.push({ label: meta.label, color: meta.color, data, isKgspin: meta.isKgspin });
    });

    if (entries.length === 0) return '';

    let html = '<div style="margin-bottom:20px;">';
    html += '<h4 style="color:#F39C12; margin:0 0 8px;">Reproducibility</h4>';
    html += '<div style="display:flex; gap:12px; flex-wrap:wrap;">';
    entries.forEach(e => {
        const similarity = e.isKgspin ? 100 : (100 - (e.data.variance_pct || 0));
        const simColor = similarity >= 90 ? '#5ED68A' : similarity >= 70 ? '#F39C12' : '#E74C3C';
        const bg = e.isKgspin ? '#0a2a0a' : '#1a1a2e';
        html += `<div style="background:${bg};padding:10px 16px;border-radius:6px;border:1px solid #2a2a4e;">`;
        html += `<span style="color:${simColor};font-size:18px;font-weight:bold;">${similarity.toFixed(1)}%</span>`;
        html += `<br><span style="color:${e.color};font-size:11px;">${e.label}</span>`;
        if (e.isKgspin) {
            html += `<br><span style="color:#5ED68A;font-size:10px;">Deterministic</span>`;
        } else {
            html += `<br><span style="color:#888;font-size:10px;">${e.data.num_runs || 0} runs</span>`;
        }
        html += '</div>';
    });
    html += '</div></div>';
    return html;
}

// ---------- Renderer: Qualitative Assessment ----------
function renderSlotQualitativeAssessment(slots, analysisData) {
    if (!analysisData) return '';
    const a = analysisData;

    let html = '<div style="margin-bottom:20px;">';
    html += '<h4 style="color:#5B9FE6; margin:0 0 10px;">Qualitative Assessment <small style="color:#888;">(by Gemini)</small></h4>';

    // Summary
    if (a.summary) {
        html += `<div style="padding:10px 14px; border-left:3px solid #5B9FE6; background:#0a0a1e; margin-bottom:14px; color:#ccc; font-size:13px; line-height:1.5;">${a.summary}</div>`;
    }

    // Per-pipeline score cards
    html += '<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px; margin-bottom:14px;">';

    // Map pipeline keys used by the analysis response
    const pipLabelMap = {kgenskills: 'KGSpin', fullshot: 'LLM Full Shot', multistage: 'LLM Multi-Stage'};
    const pipColorMap = {kgenskills: '#5ED68A', fullshot: '#E74C3C', multistage: '#F39C12'};

    // Try new format (a.pipelines) first, then legacy (a.scores)
    if (a.pipelines) {
        for (const [key, info] of Object.entries(a.pipelines)) {
            const label = pipLabelMap[key] || key;
            const color = pipColorMap[key] || '#5B9FE6';
            html += `<div style="background:#0f0f23; border:1px solid #2a2a4e; border-radius:8px; padding:12px;">`;
            html += `<h5 style="margin:0 0 8px; color:${color};">${label}</h5>`;
            if (info.precision) html += `<div style="font-size:12px;color:#ccc;"><b>Precision:</b> ${info.precision}</div>`;
            if (info.recall) html += `<div style="font-size:12px;color:#ccc;"><b>Recall:</b> ${info.recall}</div>`;
            if (info.assessment) html += `<div style="font-size:11px;color:#999;margin-top:6px;">${info.assessment}</div>`;
            html += '</div>';
        }
    } else if (a.scores) {
        for (const pip of ['kgenskills', 'fullshot', 'multistage']) {
            const p = a.scores[`${pip}_precision`];
            const r = a.scores[`${pip}_recall`];
            if (!p && !r) continue;
            const label = pipLabelMap[pip] || pip;
            const color = pipColorMap[pip] || '#5B9FE6';
            html += `<div style="background:#0f0f23; border:1px solid #2a2a4e; border-radius:8px; padding:12px;">`;
            html += `<h5 style="margin:0 0 8px; color:${color};">${label}</h5>`;
            if (p) html += `<div style="font-size:12px;color:#ccc;"><b>Precision:</b> ${p}</div>`;
            if (r) html += `<div style="font-size:12px;color:#ccc;"><b>Recall:</b> ${r}</div>`;
            html += '</div>';
        }
    }

    // Winner card
    if (a.winner && a.winner !== 'tie') {
        const winLabel = pipLabelMap[a.winner] || a.winner;
        html += `<div style="background:#0a2a0a; border:1px solid #2a4a2a; border-radius:8px; padding:12px;">`;
        html += `<h5 style="margin:0 0 6px; color:#5ED68A;">&#128081; Winner: ${winLabel}</h5>`;
        if (a.winner_reason) html += `<div style="font-size:12px;color:#ccc;">${a.winner_reason}</div>`;
        html += '</div>';
    } else if (a.winner === 'tie') {
        html += `<div style="background:#1a1a2e; border:1px solid #2a2a4e; border-radius:8px; padding:12px;">`;
        html += `<h5 style="margin:0 0 6px; color:#888;">Tie</h5>`;
        if (a.winner_reason) html += `<div style="font-size:12px;color:#ccc;">${a.winner_reason}</div>`;
        html += '</div>';
    }

    html += '</div>';

    // Cost analysis
    if (a.cost_analysis) {
        html += `<div style="padding:10px 14px; border-left:3px solid #F39C12; background:#0a0a1e; margin-bottom:14px; color:#ccc; font-size:13px;">${a.cost_analysis}</div>`;
    }

    html += '</div>';
    return html;
}

// ---------- Orchestrator: runSlotAnalysis ----------
async function runSlotAnalysis() {
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;
    const populated = slotState.filter(s => s.visData !== null);
    if (populated.length < 2) return;

    const descriptors = getSlotDescriptors();
    const cacheKey = `${ticker}|${descriptors.join('|')}`;

    const statusEl = document.getElementById('analyze-status');
    const contentEl = document.getElementById('slot-analysis-content');
    const btn = document.getElementById('analyze-btn');

    // Check cache
    if (analysisCache[cacheKey]) {
        contentEl.innerHTML = analysisCache[cacheKey];
        return;
    }

    btn.disabled = true;
    statusEl.textContent = 'Analyzing...';

    try {
        let html = '';

        // 1. Efficiency Audit (instant — uses slot stats)
        html += renderSlotEfficiencyAudit(populated);

        // 2. Comparison Matrix (instant — uses slot stats + static attributes)
        html += renderSlotComparisonMatrix(populated);

        // Show initial results immediately
        contentEl.innerHTML = html;

        // 3. Variability Scores (async — calls API)
        let varHtml = '';
        try {
            const varRes = await fetch(`/api/impact/reproducibility/${ticker}`);
            const varData = await varRes.json();
            varHtml = renderSlotVariability(populated, varData);
        } catch (e) { console.warn('Variability scores unavailable:', e); }

        // 4. Pairwise Heatmaps (async — calls API)
        let heatmapHtml = '';
        try {
            const scoresRes = await fetch(`/api/scores/${ticker}`);
            if (scoresRes.ok) {
                const scoresData = await scoresRes.json();
                heatmapHtml = renderSlotHeatmaps(populated, scoresData);
            }
        } catch (e) { console.warn('Pairwise scores unavailable:', e); }

        // 5. Qualitative Assessment (async — calls Gemini, may be slow)
        let qualHtml = '';
        try {
            statusEl.textContent = 'Running Gemini analysis...';
            const analysisRes = await fetch(`/api/refresh-analysis/${ticker}`, { method: 'POST' });
            if (analysisRes.ok) {
                const analysisData = await analysisRes.json();
                qualHtml = renderSlotQualitativeAssessment(populated, analysisData.analysis);
                // If scores came back with the analysis, use them for heatmaps if we didn't get them before
                if (!heatmapHtml && analysisData.scores) {
                    heatmapHtml = renderSlotHeatmaps(populated, analysisData.scores);
                }
            }
        } catch (e) { console.warn('Qualitative analysis unavailable:', e); }

        // Assemble full output
        html = renderSlotEfficiencyAudit(populated) + renderSlotComparisonMatrix(populated) + varHtml + heatmapHtml + qualHtml;

        contentEl.innerHTML = html;
        analysisCache[cacheKey] = html;
    } catch (err) {
        contentEl.innerHTML = `<div style="color:#E74C3C;">Analysis failed: ${err.message}</div>`;
    } finally {
        btn.disabled = false;
        statusEl.textContent = '';
        updateAnalyzeButton();
    }
}

// ============================================================
// Sprint 91: On-Demand Q&A Comparison (Task 6)
// ============================================================

const qaCache = {};

async function runSlotQA() {
    let ticker;
    if (currentDomain === 'clinical') {
        ticker = document.getElementById('trial-select').value;
    } else {
        ticker = document.getElementById('doc-id-input').value.trim().toUpperCase();
    }
    if (!ticker) return;
    const populated = slotState.filter(s => s.visData !== null);
    if (populated.length < 2) return;

    const descriptors = getSlotDescriptors();
    const cacheKey = `qa|${ticker}|${descriptors.join('|')}`;

    const statusEl = document.getElementById('qa-status');
    const contentEl = document.getElementById('slot-qa-content');
    const btn = document.getElementById('qa-btn');

    // Check cache
    if (qaCache[cacheKey]) {
        contentEl.innerHTML = qaCache[cacheKey];
        return;
    }

    btn.disabled = true;
    statusEl.textContent = 'Running Q&A...';

    const graphs = populated.map((s, idx) => ({
        pipeline: s.pipeline,
        bundle: s.bundle || 'default',
        slot_index: slotState.indexOf(s),
    }));
    const domain = currentDomain || 'financial';

    try {
        const res = await fetch(`/api/compare-qa/${ticker}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ graphs, domain }),
        });
        const data = await res.json();

        if (data.error) {
            contentEl.innerHTML = `<div style="color:#E74C3C;">${data.error}</div>`;
            return;
        }

        // Build side-by-side Q&A display
        const colCount = populated.length;
        let html = '<div style="overflow-x:auto;">';
        html += `<table style="width:100%; border-collapse:collapse; font-size:13px;">`;
        // Header row with pipeline names
        html += '<tr style="border-bottom:2px solid #2a2a4e;"><th style="text-align:left;padding:8px;color:#888;width:200px;">Question</th>';
        populated.forEach(s => {
            const meta = PIPELINE_META[s.pipeline];
            html += `<th style="text-align:left;padding:8px;color:${meta.color};">${meta.label}${s.bundle ? '<br><span style="font-size:10px;color:#888;">' + s.bundle + '</span>' : ''}</th>`;
        });
        html += '</tr>';

        // Question rows
        if (data.results && data.results.length > 0) {
            data.results.forEach(q => {
                html += '<tr style="border-bottom:1px solid #1a1a2e; vertical-align:top;">';
                html += `<td style="padding:8px;color:#ccc;font-weight:bold;">${q.question}</td>`;
                q.answers.forEach((a, i) => {
                    html += `<td style="padding:8px;color:#aaa;">${a.answer || 'No answer'}<br><span style="color:#666;font-size:10px;">${a.tokens || 0} tokens</span></td>`;
                });
                html += '</tr>';
            });
        } else {
            html += `<tr><td colspan="${colCount + 1}" style="padding:16px;color:#888;text-align:center;">No Q&A results available. Ensure pipelines have cached KG data.</td></tr>`;
        }

        html += '</table></div>';

        // Final analysis if provided
        if (data.analysis) {
            html += `<div style="margin-top:16px; padding:12px; background:#1a1a2e; border-radius:6px; border-left:3px solid #9B59B6;">`;
            html += `<h4 style="color:#9B59B6; margin:0 0 8px;">Qualitative Comparison</h4>`;
            html += `<div style="color:#ccc; font-size:13px; white-space:pre-wrap;">${data.analysis}</div>`;
            html += '</div>';
        }

        contentEl.innerHTML = html;
        qaCache[cacheKey] = html;
    } catch (err) {
        contentEl.innerHTML = `<div style="color:#E74C3C;">Q&A failed: ${err.message}</div>`;
    } finally {
        btn.disabled = false;
        statusEl.textContent = '';
        updateAnalyzeButton();
    }
}

// Wave E — compare-runner.js action registrations
registerAction('start-comparison', () => startComparison());
registerAction('run-slot-auto-flag', (el) => runSlotAutoFlag(+el.dataset.slot));
registerAction('run-slot-discover-tp', (el) => runSlotDiscoverTP(+el.dataset.slot));
registerAction('refresh-analysis', () => refreshAnalysis());
registerAction('run-slot-analysis', () => runSlotAnalysis());
registerAction('run-slot-qa', () => runSlotQA());
registerAction('run-auto-flag', () => runAutoFlag());
registerAction('load-stored-feedback', () => loadStoredFeedback());
registerAction('bulk-retract-all', () => bulkRetractAll());

// Wave F — actions for template-string handlers (auto-flag bulk/row buttons, retry buttons)
registerAction('toggle-all-auto-flags', (el) => toggleAllAutoFlags(el.checked));
registerAction('bulk-confirm-auto-flags', () => bulkConfirmAutoFlags());
registerAction('bulk-dismiss-auto-flags', () => bulkDismissAutoFlags());
registerAction('confirm-auto-tp', (el) => confirmAutoTP(el.dataset.key));
registerAction('dismiss-auto-tp', (el) => dismissAutoTP(el.dataset.key));
registerAction('go-to-flag', (el) => {
    const id = el.dataset.flagType === 'node' ? parseInt(el.dataset.flagId, 10) : el.dataset.flagId;
    goToFlag(el.dataset.pipeline, el.dataset.flagType, id);
});
registerAction('retract-stored-feedback', (el) => retractStoredFeedback(el.dataset.feedbackId, el));
registerAction('gem-refresh', () => gemRefresh());
registerAction('mod-refresh', () => modRefresh());
registerAction('kgen-refresh', () => kgenRefresh());


// --- Document Explorer (Sprint 90: per-node click from graph) ---

