// graph.js — extracted from compare.html (Wave D — JS carve)
// Behavior-preserving line-range extraction. Top-level let/const
// share global lexical scope across <script> tags. Function decls
// at top-level become window properties (used by inline on*= attrs).

// --- compare.html lines 2566-2814: Edge HITL: build/openFP/closeFP/submit/loadBundlePredicates/openFN/closeFN/updateFNSubmitState/submitFN/retract ---
function buildFeedbackButton(pipeline, edgeId) {
    const flagState = getFeedbackState(pipeline, edgeId);
    const backendType = resolveBackendType(pipeline);
    if (backendType === 'kgspin') {
        if (flagState === 'fp') {
            return `<div style="margin-top:12px;"><button onclick="retractFeedback('${pipeline}', '${edgeId}')" style="width:100%;padding:8px;background:#2a2a4e;color:#aaa;border:1px solid #3a3a5e;border-radius:6px;cursor:pointer;font-size:12px;">Retract Flag</button></div>`;
        }
        return `<div style="margin-top:12px;"><button onclick="openFPModal('${pipeline}', '${edgeId}')" style="width:100%;padding:8px;background:#5a2a2a;color:#ff6b6b;border:none;border-radius:6px;cursor:pointer;font-size:13px;">Flag as Incorrect</button></div>`;
    } else if (backendType === 'llm') {
        if (flagState === 'fn') {
            return `<div style="margin-top:12px;"><button onclick="retractFeedback('${pipeline}', '${edgeId}')" style="width:100%;padding:8px;background:#2a2a4e;color:#aaa;border:1px solid #3a3a5e;border-radius:6px;cursor:pointer;font-size:12px;">Retract Flag</button></div>`;
        }
        return `<div style="margin-top:12px;"><button onclick="openFNModal('${pipeline}', '${edgeId}')" style="width:100%;padding:8px;background:#3a3017;color:#d4a017;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;">Save to Gold Dataset</button></div>`;
    }
    // Unknown pipeline — show both FP and FN options
    return `<div style="margin-top:12px; display:flex; gap:6px;">
        <button onclick="openFPModal('${pipeline}', '${edgeId}')" style="flex:1;padding:8px;background:#5a2a2a;color:#ff6b6b;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Flag as Incorrect</button>
        <button onclick="openFNModal('${pipeline}', '${edgeId}')" style="flex:1;padding:8px;background:#3a3017;color:#d4a017;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Save to Gold</button>
    </div>`;
}

// --- FP Modal ---
function openFPModal(pipeline, edgeId) {
    const edges = edgeDataSets[pipeline];
    const nodes = nodeDataSets[pipeline];
    if (!edges || !nodes) return;
    const edge = edges.get(edgeId);
    if (!edge || !edge.metadata) return;
    const meta = edge.metadata;
    const subjNode = nodes.get(meta.subject_id);
    const objNode = nodes.get(meta.object_id);
    const subjName = meta.subject_text || (subjNode ? subjNode.label : '?');
    const objName = meta.object_text || (objNode ? objNode.label : '?');
    document.getElementById('fp-extraction-summary').innerHTML =
        `<strong>${subjName}</strong> &mdash; <em>${meta.predicate}</em> &mdash; <strong>${objName}</strong><br>` +
        `Confidence: ${(meta.confidence * 100).toFixed(0)}%` +
        (meta.evidence_text ? `<br>Evidence: "${meta.evidence_text}"` : '');
    document.querySelectorAll('#fp-reasons input[type="checkbox"]').forEach(cb => cb.checked = false);
    document.getElementById('fp-reason-detail').value = '';
    updateFPSubmitState();
    fpModalContext = { pipeline, edgeId, meta, subjName, objName };
    document.getElementById('fp-modal').style.display = 'flex';
}

function closeFPModal() {
    document.getElementById('fp-modal').style.display = 'none';
    window._autoEdgeFlagKey = null;
    fpModalContext = null;
}

async function submitFalsePositive() {
    if (!fpModalContext) return;
    const { pipeline, edgeId, meta, subjName, objName } = fpModalContext;
    const btn = document.getElementById('fp-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';
    try {
        const resp = await fetch('/api/feedback/false_positive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: state.docId || '',
                pipeline: pipeline,
                subject_text: subjName,
                subject_type: '',
                predicate: meta.predicate,
                object_text: objName,
                object_type: '',
                confidence: meta.confidence,
                evidence_sentence: meta.evidence_text || '',
                source_document: meta.source_document || '',
                chunk_id: '',
                extraction_method: meta.extraction_method || '',
                reasons: Array.from(document.querySelectorAll('#fp-reasons input:checked')).map(cb => cb.value),
                reason_detail: document.getElementById('fp-reason-detail').value,
            }),
        });
        const data = await resp.json();
        // Remove auto-flag entry if this came from Edit flow
        if (window._autoEdgeFlagKey) {
            delete feedbackState[window._autoEdgeFlagKey];
            window._autoEdgeFlagKey = null;
        }
        feedbackState[`${pipeline}_${edgeId}`] = { type: 'fp', feedbackId: data.id, label: `${subjName} → ${meta.predicate} → ${objName}` };
        // Turn edge red
        edgeDataSets[pipeline].update({ id: edgeId, color: { color: '#ff6b6b', highlight: '#ff6b6b' } });
        closeFPModal();
        closeDetailPanel();
        showToast('Flagged as False Positive', 'fp');
        renderFlagExplorer();
    } catch (e) {
        console.error('FP submit error:', e);
        showToast('Error submitting feedback', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Flag as Incorrect';
    }
}

// --- FN Modal ---
async function loadBundlePredicates() {
    if (bundlePredicates) return;
    try {
        const resp = await fetch('/api/bundle/predicates');
        const data = await resp.json();
        bundlePredicates = data.predicates;
        const select = document.getElementById('fn-predicate');
        select.innerHTML = '<option value="">-- Select from bundle schema --</option>';
        for (const p of bundlePredicates) {
            select.innerHTML += `<option value="${p.name}" title="${p.definition}">${p.name}</option>`;
        }
    } catch (e) {
        console.error('Failed to load predicates:', e);
    }
}

async function openFNModal(pipeline, edgeId) {
    await loadBundlePredicates();
    const edges = edgeDataSets[pipeline];
    const nodes = nodeDataSets[pipeline];
    if (!edges || !nodes) return;
    const edge = edges.get(edgeId);
    const meta = (edge && edge.metadata) || (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][edgeId]);
    if (!edge || !meta) return;
    const subjNode = nodes.get(meta.subject_id);
    const objNode = nodes.get(meta.object_id);
    const subjName = meta.subject_text || (subjNode ? subjNode.label : '?');
    const objName = meta.object_text || (objNode ? objNode.label : '?');
    document.getElementById('fn-extraction-summary').innerHTML =
        `<strong>${subjName}</strong> &mdash; <em>${meta.predicate}</em> &mdash; <strong>${objName}</strong>`;
    // Pre-select predicate if it matches a bundle predicate
    const predSelect = document.getElementById('fn-predicate');
    predSelect.value = meta.predicate || '';
    // Sprint 39.3: Fuzzy match if exact match failed (case-insensitive + stem)
    if (!predSelect.value && meta.predicate && bundlePredicates) {
        const lowerPred = meta.predicate.toLowerCase().replace(/s$/, '');
        const match = bundlePredicates.find(p =>
            p.name.toLowerCase() === meta.predicate.toLowerCase() ||
            p.name.toLowerCase().replace(/s$/, '') === lowerPred
        );
        if (match) predSelect.value = match.name;
    }
    // Sprint 39.2: Evidence highlight-to-confirm (replaces paste textarea)
    const fullEvidence = meta.full_evidence_text || meta.evidence_text || '(no evidence available)';
    document.getElementById('fn-chunk-viewer').textContent = fullEvidence;
    document.getElementById('fn-selected-evidence').innerHTML = '<em style="color:#888;">No text selected. Highlight supporting text above.</em>';
    document.getElementById('fn-evidence-confirmed').value = '';
    updateFNSubmitState();
    fnModalContext = { pipeline, edgeId, meta, subjName, objName };
    document.getElementById('fn-modal').style.display = 'flex';
}

function closeFNModal() {
    document.getElementById('fn-modal').style.display = 'none';
    fnModalContext = null;
}

function updateFNSubmitState() {
    const predicate = document.getElementById('fn-predicate').value;
    const evidence = document.getElementById('fn-evidence-confirmed').value;
    // VP Eng guardrail G4: predicate required + evidence must be highlighted
    document.getElementById('fn-submit-btn').disabled = !predicate || !evidence;
}

// Wire up predicate dropdown change
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('fn-predicate').addEventListener('change', updateFNSubmitState);
});

async function submitFalseNegative() {
    if (!fnModalContext) return;
    const { pipeline, edgeId, meta, subjName, objName } = fnModalContext;
    const predicate = document.getElementById('fn-predicate').value;
    const evidence = document.getElementById('fn-evidence-confirmed').value;
    if (!predicate || !evidence) { return; }
    const btn = document.getElementById('fn-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';
    try {
        const resp = await fetch('/api/feedback/false_negative', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: state.docId || '',
                pipeline: pipeline,
                subject_text: subjName,
                subject_type: '',
                predicate: predicate,
                object_text: objName,
                object_type: '',
                evidence_sentence: evidence,
                source_document: meta.source_document || '',
                original_confidence: meta.confidence || 0,
                original_evidence: meta.evidence_text || '',
            }),
        });
        const data = await resp.json();
        if (resp.status === 400) {
            showToast(data.error || 'Invalid predicate or evidence', 'error');
            return;
        }
        feedbackState[`${pipeline}_${edgeId}`] = { type: 'fn', feedbackId: data.id, label: `${subjName} → ${predicate} → ${objName}` };
        // Turn edge gold
        edgeDataSets[pipeline].update({ id: edgeId, color: { color: '#d4a017', highlight: '#d4a017' } });
        closeFNModal();
        closeDetailPanel();
        showToast('Saved to Gold Dataset', 'fn');
        renderFlagExplorer();
    } catch (e) {
        console.error('FN submit error:', e);
        showToast('Error submitting feedback', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save to Gold Dataset';
        updateFNSubmitState();
    }
}

// --- Retraction ---
async function retractFeedback(pipeline, edgeId) {
    const key = `${pipeline}_${edgeId}`;
    const entry = feedbackState[key];
    if (!entry) return;
    try {
        const resp = await fetch('/api/feedback/retract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback_id: entry.feedbackId }),
        });
        const data = await resp.json();
        if (data.status === 'retracted') {
            delete feedbackState[key];
            // Restore original edge color
            const edge = edgeDataSets[pipeline].get(edgeId);
            const pred = edge && edge.metadata ? edge.metadata.predicate : '';
            const origColor = REL_COLORS[pred] || '#AAA';
            edgeDataSets[pipeline].update({ id: edgeId, color: { color: origColor, highlight: origColor } });
            closeDetailPanel();
            showToast('Feedback retracted', 'info');
            renderFlagExplorer();
        }
    } catch (e) {
        console.error('Retract error:', e);
        showToast('Error retracting feedback', 'error');
    }
}

// --- Entity-level Feedback ---
// vis.js DataSet uses integer IDs from backend; HTML onclick stringifies them

// --- compare.html lines 2815-2818: getVisNodeId ---
function getVisNodeId(nodeId) {
    const parsed = parseInt(nodeId, 10);
    return isNaN(parsed) ? nodeId : parsed;
}

// --- compare.html lines 2819-2819: entityFPContext ---
let entityFPContext = null;

// --- compare.html lines 2820-3054: Entity HITL: buildAutoFlagAlert/buildEntity*Btn/openEntityFP/closeEntityFP/submitEntityFP/retractEntityFeedback/flagEntityTP ---

// Sprint 39.2 Item 5: Auto-flag alert in detail panel
function buildAutoFlagAlert(pipeline, type, id) {
    const key = type === 'node' ? `auto_entity_${pipeline}_${id}` : `auto_${pipeline}_${id}`;
    const entry = feedbackState[key];
    if (!entry || (entry.type !== 'auto_fp' && entry.type !== 'auto_entity_fp' && entry.type !== 'auto_edge_fp')) return '';
    const _alertReasonLabels = { not_a_proper_noun: 'Not a Proper Noun (Do Not Extract)', resolvable_descriptor: 'Not a Proper Noun (Resolvable Descriptor)', wrong_entity_type: 'Wrong Entity Type' };
    const reasonsText = (entry.reasons || []).map(r => _alertReasonLabels[r] || r).join(', ') || 'Suspicious extraction';
    const detailText = entry.reason_detail ? `<div style="margin-top:4px;font-size:12px;color:#ccc;">${entry.reason_detail}</div>` : '';
    return `
        <div style="margin-top:12px;padding:10px;background:#FF8C0018;border:1px solid #FF8C0044;border-radius:8px;">
            <div style="font-size:13px;font-weight:600;color:#FF8C00;">&#9888; AI Auto-Flagged</div>
            <div style="font-size:12px;color:#FFB347;margin-top:4px;">Reasons: ${reasonsText}</div>
            ${detailText}
            <div style="display:flex;gap:6px;margin-top:8px;">
                <button onclick="confirmAutoFlag('${pipeline}', '${type}', '${id}')" style="flex:1;padding:6px;background:#ff6b6b;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Confirm</button>
                <button onclick="confirmAutoFlagWithEdits('${pipeline}', '${type}', '${id}')" style="flex:1;padding:6px;background:#3a3017;color:#d4a017;border:1px solid #5a4a17;border-radius:4px;cursor:pointer;font-size:12px;">Edit & Confirm</button>
                <button onclick="dismissAutoFlag('${pipeline}', '${type}', '${id}')" style="flex:1;padding:6px;background:#2a2a4e;color:#aaa;border:1px solid #3a3a5e;border-radius:4px;cursor:pointer;font-size:12px;">Dismiss</button>
            </div>
        </div>`;
}

function buildEntityFeedbackButton(pipeline, nodeId) {
    // Sprint 90: FP flagging from any graph panel (not just KGSpin)
    const key = `entity_${pipeline}_${nodeId}`;
    if (feedbackState[key]) {
        return `<div style="margin-top:12px;"><button onclick="retractEntityFeedback('${pipeline}', '${nodeId}')" style="width:100%;padding:8px;background:#2a2a4e;color:#aaa;border:1px solid #3a3a5e;border-radius:6px;cursor:pointer;font-size:12px;">Retract Entity Flag</button></div>`;
    }
    // Sprint 90: FP + TP buttons from any pipeline
    return `<div style="margin-top:12px; display:flex; gap:6px;">
        <button onclick="openEntityFPModal('${pipeline}', '${nodeId}')" style="flex:1;padding:8px;background:#5a2a2a;color:#ff6b6b;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Flag as FP</button>
        <button onclick="flagEntityTP('${pipeline}', '${nodeId}')" style="flex:1;padding:8px;background:#1a3a1a;color:#5ED68A;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Confirm as TP</button>
    </div>`;
}

// Sprint 39.3: Entity-level FN (Save LLM entity to gold dataset)
function buildEntityFNButton(pipeline, nodeId) {
    // Sprint 90: FN flagging from any pipeline
    const key = `entity_fn_${pipeline}_${nodeId}`;
    if (feedbackState[key]) {
        return `<div style="margin-top:12px;"><button onclick="retractEntityFeedback('${pipeline}', '${nodeId}', 'fn')" style="width:100%;padding:8px;background:#2a2a4e;color:#aaa;border:1px solid #3a3a5e;border-radius:6px;cursor:pointer;font-size:12px;">Retract Gold Entity</button></div>`;
    }
    return `<div style="margin-top:6px;"><button onclick="openEntityFNModal('${pipeline}', '${nodeId}')" style="width:100%;padding:8px;background:#3a3017;color:#d4a017;border:none;border-radius:6px;cursor:pointer;font-size:12px;">Save Entity to Gold Dataset</button></div>`;
}

function openEntityFPModal(pipeline, nodeId) {
    const nodes = nodeDataSets[pipeline];
    if (!nodes) return;
    const visId = getVisNodeId(nodeId);
    const node = nodes.get(visId);
    const meta = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
    if (!meta) { console.warn('[openEntityFPModal] no metadata:', pipeline, nodeId); return; }
    document.getElementById('entity-fp-summary').innerHTML =
        `<strong>${meta.text}</strong><br>Type: ${meta.entity_type} | Confidence: ${(meta.confidence * 100).toFixed(0)}% | Mentions: ${meta.mention_count}`;
    document.querySelectorAll('#entity-fp-reasons input[type="checkbox"]').forEach(cb => cb.checked = false);
    document.getElementById('entity-fp-reason-detail').value = '';
    // Populate corrected_type dropdown with hierarchical bundle entity types
    const sel = document.getElementById('entity-fp-corrected-type');
    const hierarchy = window._bundleTypeHierarchy;
    let types = window._bundleEntityTypes;
    if (!types || !types.length) {
        // Fallback: derive from graph node types (these ARE bundle types)
        const graphTypes = new Set();
        if (nodeMetaMaps[pipeline]) {
            Object.values(nodeMetaMaps[pipeline]).forEach(m => { if (m.entity_type && m.entity_type !== 'UNKNOWN') graphTypes.add(m.entity_type); });
        }
        types = [...graphTypes].sort();
    }
    let optionsHtml = '<option value="">-- Select corrected type --</option><option value="DO_NOT_EXTRACT">DO_NOT_EXTRACT (suppress this entity)</option>';
    if (hierarchy && Object.keys(hierarchy).length > 0) {
        // Grouped dropdown with optgroups
        for (const parent of Object.keys(hierarchy).sort()) {
            const subs = hierarchy[parent].sort();
            optionsHtml += `<optgroup label="${parent}">`;
            for (const s of subs) {
                optionsHtml += `<option value="${s}">${parent} → ${s}</option>`;
            }
            optionsHtml += '</optgroup>';
        }
    } else {
        // Flat fallback
        optionsHtml += types.map(t => `<option value="${t}">${t}</option>`).join('');
    }
    sel.innerHTML = optionsHtml;
    sel.value = '';
    document.getElementById('entity-fp-corrected-type-row').style.display = 'none';
    document.getElementById('entity-fp-noun-action').value = '';
    document.getElementById('entity-fp-noun-action-row').style.display = 'none';
    updateEntityFPSubmitState();
    entityFPContext = { pipeline, nodeId, meta };
    document.getElementById('entity-fp-modal').style.display = 'flex';
}

function closeEntityFPModal() {
    document.getElementById('entity-fp-modal').style.display = 'none';
    entityFPContext = null;
}

async function submitEntityFP() {
    if (!entityFPContext) return;
    const { pipeline, nodeId, meta } = entityFPContext;
    let reasons = Array.from(document.querySelectorAll('#entity-fp-reasons input:checked')).map(cb => cb.value);
    if (reasons.length === 0) return;
    // Sprint 50: Resolve not_a_proper_noun sub-classification from dropdown
    if (reasons.includes('not_a_proper_noun')) {
        const nounAction = document.getElementById('entity-fp-noun-action').value;
        if (nounAction === 'resolvable_descriptor') {
            reasons = reasons.map(r => r === 'not_a_proper_noun' ? 'resolvable_descriptor' : r);
        }
        // 'do_not_extract' keeps the reason as 'not_a_proper_noun' (original behavior)
    }
    const reasonDetail = document.getElementById('entity-fp-reason-detail').value.trim();
    const correctedType = reasons.includes('wrong_entity_type') ? document.getElementById('entity-fp-corrected-type').value : '';
    const btn = document.getElementById('entity-fp-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';
    try {
        const resp = await fetch('/api/feedback/false_positive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: state.docId || '',
                pipeline: pipeline,
                feedback_target: 'entity',
                subject_text: meta.text,
                subject_type: meta.entity_type,
                predicate: '',
                object_text: '',
                object_type: '',
                confidence: meta.confidence || 0,
                evidence_sentence: '',
                extraction_method: '',
                reasons: reasons,
                reason_detail: reasonDetail,
                corrected_type: correctedType,
            }),
        });
        const data = await resp.json();
        // Remove auto-flag entry if this came from Edit flow
        if (entityFPContext._autoFlagKey) {
            delete feedbackState[entityFPContext._autoFlagKey];
        }
        feedbackState[`entity_${pipeline}_${nodeId}`] = { type: 'entity_fp', feedbackId: data.id, label: meta.text };
        // Turn node red — preserve position to prevent physics jump
        const visIdFP = getVisNodeId(nodeId);
        const curNodeFP = nodeDataSets[pipeline].get(visIdFP);
        const fpUpdate = { id: visIdFP, color: { background: '#ff6b6b', border: '#ff3333', highlight: { background: '#ff6b6b', border: '#ff3333' } } };
        if (curNodeFP) { fpUpdate.x = curNodeFP.x; fpUpdate.y = curNodeFP.y; fpUpdate.fixed = { x: true, y: true }; }
        nodeDataSets[pipeline].update(fpUpdate);
        if (curNodeFP) setTimeout(() => { nodeDataSets[pipeline].update({ id: visIdFP, fixed: { x: false, y: false } }); }, 500);
        closeEntityFPModal();
        closeDetailPanel();
        showToast('Entity flagged as incorrect', 'fp');
        renderFlagExplorer();
    } catch (e) {
        console.error('Entity FP submit error:', e);
        showToast('Error submitting entity feedback', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Flag Entity';
    }
}

async function retractEntityFeedback(pipeline, nodeId, feedbackType) {
    // Sprint 39.3: support both FP and FN entity keys
    const key = feedbackType === 'fn' ? `entity_fn_${pipeline}_${nodeId}` : `entity_${pipeline}_${nodeId}`;
    const entry = feedbackState[key];
    if (!entry) return;
    try {
        const resp = await fetch('/api/feedback/retract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback_id: entry.feedbackId }),
        });
        const data = await resp.json();
        if (data.status === 'retracted') {
            delete feedbackState[key];
            // Restore original node color
            const visIdRetract = getVisNodeId(nodeId);
            const nodeRetract = nodeDataSets[pipeline].get(visIdRetract);
            const meta = (nodeRetract && nodeRetract.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
            const entityType = meta ? meta.entity_type : '';
            const origColor = TYPE_COLORS[entityType] || '#AAA';
            const retractUpdate = { id: visIdRetract, color: { background: origColor, border: origColor, highlight: { background: origColor, border: origColor } } };
            if (nodeRetract) { retractUpdate.x = nodeRetract.x; retractUpdate.y = nodeRetract.y; retractUpdate.fixed = { x: true, y: true }; }
            nodeDataSets[pipeline].update(retractUpdate);
            if (nodeRetract) setTimeout(() => { nodeDataSets[pipeline].update({ id: visIdRetract, fixed: { x: false, y: false } }); }, 500);
            closeDetailPanel();
            showToast('Entity flag retracted', 'info');
            renderFlagExplorer();
        }
    } catch (e) {
        console.error('Entity retract error:', e);
        showToast('Error retracting entity feedback', 'error');
    }
}

// Sprint 90: Confirm entity as True Positive
async function flagEntityTP(pipeline, nodeId) {
    const nodes = nodeDataSets[pipeline];
    if (!nodes) return;
    const visId = getVisNodeId(nodeId);
    const node = nodes.get(visId);
    const meta = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
    if (!meta) { console.warn('[flagEntityTP] no metadata:', pipeline, nodeId); return; }
    try {
        const resp = await fetch('/api/feedback/true_positive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: state.docId || '',
                pipeline: pipeline,
                subject_text: meta.text,
                subject_type: meta.entity_type,
                confidence: meta.confidence || 0,
            }),
        });
        const data = await resp.json();
        feedbackState[`entity_${pipeline}_${nodeId}`] = { type: 'entity_tp', feedbackId: data.id, label: meta.text };
        // Turn node green to indicate confirmed TP — preserve position
        const curNode = nodes.get(visId);
        const tpUpdate = { id: visId, color: { background: '#5ED68A', border: '#2ecc71', highlight: { background: '#5ED68A', border: '#2ecc71' } } };
        if (curNode) { tpUpdate.x = curNode.x; tpUpdate.y = curNode.y; tpUpdate.fixed = { x: true, y: true }; }
        nodes.update(tpUpdate);
        if (curNode) setTimeout(() => { nodes.update({ id: visId, fixed: { x: false, y: false } }); }, 500);
        closeDetailPanel();
        showToast('Entity confirmed as correct', 'success');
        renderFlagExplorer();
    } catch (e) {
        console.error('Entity TP submit error:', e);
        showToast('Error confirming entity', 'error');
    }
}

// Sprint 39.3: Entity-level FN modal functions

// --- compare.html lines 3055-3128: entityFNContext + openEntityFN + closeEntityFN + submitEntityFN + updateFPSubmitState ---
let entityFNContext = null;

function openEntityFNModal(pipeline, nodeId) {
    const nodes = nodeDataSets[pipeline];
    if (!nodes) return;
    const visId = getVisNodeId(nodeId);
    const node = nodes.get(visId);
    const meta = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
    if (!meta) return;
    document.getElementById('entity-fn-summary').innerHTML =
        `<strong>${meta.text}</strong>`;
    document.getElementById('entity-fn-type').textContent = meta.entity_type;
    document.getElementById('entity-fn-notes').value = '';
    entityFNContext = { pipeline, nodeId, meta };
    document.getElementById('entity-fn-modal').style.display = 'flex';
}

function closeEntityFNModal() {
    document.getElementById('entity-fn-modal').style.display = 'none';
    entityFNContext = null;
}

async function submitEntityFN() {
    if (!entityFNContext) return;
    const { pipeline, nodeId, meta } = entityFNContext;
    const btn = document.getElementById('entity-fn-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Submitting...';
    try {
        const resp = await fetch('/api/feedback/false_negative', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                document_id: state.docId || '',
                pipeline: pipeline,
                feedback_target: 'entity',
                subject_text: meta.text,
                subject_type: meta.entity_type,
                predicate: '',
                object_text: '',
                object_type: '',
                evidence_sentence: '',
                source_document: '',
                original_confidence: meta.confidence || 0,
                original_evidence: '',
            }),
        });
        const data = await resp.json();
        feedbackState[`entity_fn_${pipeline}_${nodeId}`] = { type: 'entity_fn', feedbackId: data.id, label: meta.text };
        // Turn node gold — preserve position to prevent physics jump
        const visIdFN = getVisNodeId(nodeId);
        const curNodeFN = nodeDataSets[pipeline].get(visIdFN);
        const fnUpdate = { id: visIdFN, color: { background: '#d4a017', border: '#FFD700', highlight: { background: '#d4a017', border: '#FFD700' } } };
        if (curNodeFN) { fnUpdate.x = curNodeFN.x; fnUpdate.y = curNodeFN.y; fnUpdate.fixed = { x: true, y: true }; }
        nodeDataSets[pipeline].update(fnUpdate);
        if (curNodeFN) setTimeout(() => { nodeDataSets[pipeline].update({ id: visIdFN, fixed: { x: false, y: false } }); }, 500);
        closeEntityFNModal();
        closeDetailPanel();
        showToast('Entity saved to gold dataset', 'fn');
        renderFlagExplorer();
    } catch (e) {
        console.error('Entity FN submit error:', e);
        showToast('Error submitting entity feedback', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save to Gold Dataset';
    }
}

// === Sprint 39.2: Submit state validators for multi-select checkboxes ===
function updateFPSubmitState() {
    const checked = document.querySelectorAll('#fp-reasons input:checked').length;
    document.getElementById('fp-submit-btn').disabled = checked === 0;
}

// --- compare.html lines 3129-3178: updateEntityFPSubmitState ---
function updateEntityFPSubmitState() {
    const checkedBoxes = document.querySelectorAll('#entity-fp-reasons input:checked');
    const reasons = Array.from(checkedBoxes).map(cb => cb.value);
    const hasWrongType = reasons.includes('wrong_entity_type');
    const hasNotProperNoun = reasons.includes('not_a_proper_noun');
    // Show/hide corrected_type dropdown for wrong_entity_type
    document.getElementById('entity-fp-corrected-type-row').style.display = hasWrongType ? 'block' : 'none';
    if (!hasWrongType) document.getElementById('entity-fp-corrected-type').value = '';
    // Show/hide noun action dropdown for not_a_proper_noun
    document.getElementById('entity-fp-noun-action-row').style.display = hasNotProperNoun ? 'block' : 'none';
    if (!hasNotProperNoun) document.getElementById('entity-fp-noun-action').value = '';
    // Validate: reasons checked + sub-selections made
    const correctedType = document.getElementById('entity-fp-corrected-type').value;
    const nounAction = document.getElementById('entity-fp-noun-action').value;
    const valid = reasons.length > 0
        && (!hasWrongType || correctedType)
        && (!hasNotProperNoun || nounAction);
    document.getElementById('entity-fp-submit-btn').disabled = !valid;
}

// === Sprint 39.2: Evidence highlight-to-confirm (VP Eng guardrail G2) ===
document.addEventListener('DOMContentLoaded', () => {
    const viewer = document.getElementById('fn-chunk-viewer');
    if (viewer) {
        viewer.addEventListener('mouseup', function() {
            const sel = window.getSelection();
            if (sel && sel.toString().trim().length > 0) {
                const selectedText = sel.toString().trim();
                const fullText = this.textContent;
                // VP Eng guardrail G2: validate substring + min length
                if (selectedText.length < 10) {
                    document.getElementById('fn-selected-evidence').innerHTML =
                        '<em style="color:#ff6b6b;">Selection too short (min 10 chars)</em>';
                    return;
                }
                if (!fullText.includes(selectedText)) {
                    document.getElementById('fn-selected-evidence').innerHTML =
                        '<em style="color:#ff6b6b;">Selection must come from the text above</em>';
                    return;
                }
                document.getElementById('fn-selected-evidence').innerHTML =
                    `<span style="color:#d4a017;">&ldquo;${selectedText}&rdquo;</span>`;
                document.getElementById('fn-evidence-confirmed').value = selectedText;
                updateFNSubmitState();
            }
        });
    }
});

// === Sprint 39.3: Full Document Viewer with Search ===

// --- compare.html lines 3179-3316: Doc viewer (text/search/etc.) ---
let docViewerText = '';
let docSearchMatches = [];
let docSearchCurrent = -1;
let docSearchTimer = null;

async function openDocViewer() {
    const ticker = state.docId;
    if (!ticker) { showToast('No ticker loaded', 'error'); return; }
    document.getElementById('doc-viewer-modal').style.display = 'flex';
    document.getElementById('doc-viewer-text').textContent = 'Loading document...';
    document.getElementById('doc-search-input').value = '';
    document.getElementById('doc-search-count').textContent = '';
    document.getElementById('doc-viewer-selection').innerHTML = '<em style="color:#888;">No text selected.</em>';
    document.getElementById('doc-viewer-confirm-btn').disabled = true;
    docSearchMatches = [];
    docSearchCurrent = -1;
    try {
        const resp = await fetch(`/api/document/text/${ticker}`);
        const data = await resp.json();
        if (data.error) {
            document.getElementById('doc-viewer-text').textContent = 'Error: ' + data.error;
            return;
        }
        docViewerText = data.text;
        document.getElementById('doc-viewer-text').textContent = docViewerText;
        setTimeout(() => document.getElementById('doc-search-input').focus(), 100);
    } catch (e) {
        document.getElementById('doc-viewer-text').textContent = 'Failed to load document text.';
    }
}

function closeDocViewer() {
    document.getElementById('doc-viewer-modal').style.display = 'none';
}

// Evidence selection from document viewer
document.addEventListener('DOMContentLoaded', () => {
    const docText = document.getElementById('doc-viewer-text');
    if (docText) {
        docText.addEventListener('mouseup', function() {
            const sel = window.getSelection();
            if (sel && sel.toString().trim().length >= 10) {
                const selectedText = sel.toString().trim();
                document.getElementById('doc-viewer-selection').innerHTML =
                    `<span style="color:#d4a017;">&ldquo;${selectedText}&rdquo;</span>`;
                document.getElementById('doc-viewer-confirm-btn').disabled = false;
                document.getElementById('doc-viewer-confirm-btn').dataset.evidence = selectedText;
            }
        });
    }
});

function confirmDocViewerSelection() {
    const evidence = document.getElementById('doc-viewer-confirm-btn').dataset.evidence;
    if (!evidence) return;
    // Push selected evidence back to the FN modal
    document.getElementById('fn-selected-evidence').innerHTML =
        `<span style="color:#d4a017;">&ldquo;${evidence}&rdquo;</span>`;
    document.getElementById('fn-evidence-confirmed').value = evidence;
    updateFNSubmitState();
    closeDocViewer();
}

function docSearchDebounced() {
    clearTimeout(docSearchTimer);
    docSearchTimer = setTimeout(docSearchExecute, 300);
}

function docSearchExecute() {
    const query = document.getElementById('doc-search-input').value.trim();
    const viewer = document.getElementById('doc-viewer-text');
    if (!query || query.length < 2) {
        viewer.textContent = docViewerText;
        document.getElementById('doc-search-count').textContent = '';
        docSearchMatches = [];
        docSearchCurrent = -1;
        return;
    }
    // Find all match positions
    const lowerText = docViewerText.toLowerCase();
    const lowerQuery = query.toLowerCase();
    docSearchMatches = [];
    let pos = 0;
    while ((pos = lowerText.indexOf(lowerQuery, pos)) !== -1) {
        docSearchMatches.push(pos);
        pos += lowerQuery.length;
    }
    document.getElementById('doc-search-count').textContent =
        docSearchMatches.length > 0 ? `${docSearchMatches.length} matches` : 'No matches';
    if (docSearchMatches.length > 0) {
        docSearchCurrent = 0;
        renderDocSearchHighlights(query);
    } else {
        viewer.textContent = docViewerText;
        docSearchCurrent = -1;
    }
}

function escapeHtmlForSearch(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderDocSearchHighlights(query) {
    const viewer = document.getElementById('doc-viewer-text');
    const escaped = escapeHtmlForSearch(docViewerText);
    const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    let matchIdx = 0;
    const html = escaped.replace(regex, (match) => {
        const isCurrent = matchIdx === docSearchCurrent;
        matchIdx++;
        const bg = isCurrent ? '#FF8C00' : '#FFD70066';
        const id = isCurrent ? 'id="doc-search-current"' : '';
        return `<mark ${id} style="background:${bg};color:#000;padding:0 1px;border-radius:2px;">${match}</mark>`;
    });
    viewer.innerHTML = html;
    const current = document.getElementById('doc-search-current');
    if (current) current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    document.getElementById('doc-search-count').textContent =
        `${docSearchCurrent + 1}/${docSearchMatches.length}`;
}

function docSearchNext() {
    if (docSearchMatches.length === 0) return;
    docSearchCurrent = (docSearchCurrent + 1) % docSearchMatches.length;
    const query = document.getElementById('doc-search-input').value.trim();
    renderDocSearchHighlights(query);
}

function docSearchPrev() {
    if (docSearchMatches.length === 0) return;
    docSearchCurrent = (docSearchCurrent - 1 + docSearchMatches.length) % docSearchMatches.length;
    const query = document.getElementById('doc-search-input').value.trim();
    renderDocSearchHighlights(query);
}

// === Sprint 39.2 Item 5: AI Auto-Flag (updated Sprint 120: per-slot discovery) ===

// Slot-aware FP discovery — called from each graph toolbar

// --- compare.html lines 4190-4250: ACTOR_TYPES + NOISE_COLOR + TYPE_COLORS + REL_COLORS ---
let ACTOR_TYPES = new Set();  // populated by loadSchema()
const NOISE_COLOR = '#6B3A3A';

const TYPE_COLORS = {
    // Parent types (spaCy NER labels)
    PERSON: '#5B9FE6', ORG: '#5ED68A', ORGANIZATION: '#5ED68A',
    PRODUCT: '#C45BE6', GPE: '#7BE65B', LAW: '#E6C45B',
    EVENT: '#FF7F6B', DISEASE: '#7BE65B', NORP: '#C49AFF',
    LOCATION: '#4DD4C0',
    // Financial domain subtypes (current bundle schema)
    COMPANY: '#5ED68A', CORPORATE_LEADER: '#5B9FE6',
    COMMERCIAL_OFFERING: '#C45BE6',
    ACT_OR_REGULATION: '#E6C45B', GEOPOLITICAL_ENTITY: '#7BE65B',
    GOVERNMENT_AGENCY: '#E6855B', FINANCIAL_EXCHANGE: '#FFB347',
    NAMED_INSTITUTION: '#4DD4C0',
    // Legacy subtypes (for older bundles)
    EXECUTIVE: '#5B9FE6', EMPLOYEE: '#5B9FE6',
    REGULATOR: '#E6855B', BRANDED_PRODUCT: '#C45BE6',
    MARKET: '#7BE65B', OFFICE: '#4DD4C0',
    // Clinical domain subtypes
    DRUG: '#C45BE6', CONDITION: '#7BE65B',
    DRUG_COMPOUND: '#C45BE6', MEDICAL_RESEARCH_ORG: '#E6855B',
    CLINICAL_TRIAL: '#FF7F6B', REGULATORY_BODY: '#E6855B',
    NATIONALITY: '#C49AFF',
    ENDPOINT: '#81C784', BIOMARKER: '#64B5F6',
    INVESTIGATOR: '#BA68C8', PROCEDURE: '#4DD0E1',
    UNKNOWN: '#AAAAAA',
};

const REL_COLORS = {
    // Financial domain (current bundle predicates)
    holds_position_at: '#5B9FE6',
    is_subsidiary_of: '#4DD4C0',
    acquired: '#FF7F6B',
    divested: '#C49A6C',
    offers: '#C45BE6',
    competes_with: '#FFE066',
    is_subject_to: '#E6C45B',
    operates_in: '#E088E5',
    is_regulated_by: '#FF6B8A',
    contributes_to: '#B8E986',
    // Legacy predicates (for older bundles)
    is_executive: '#5B9FE6',
    regulated_by: '#FF6B8A',
    has_offices: '#B8E986',
    partners_with: '#6B8FFF',
    sells: '#FFB347',
    procures: '#4DD4C0',
    // Clinical domain predicates
    develops: '#E6855B',
    treats: '#7BE65B',
    sponsors: '#FFB347',
    investigates: '#81C784',
    has_endpoint: '#FFB74D',
    adverse_event: '#FF6B8A',
    compared_to: '#64B5F6',
    sponsors: '#AED581',
    manufactured_by: '#BA68C8',
    has_biomarker: '#4DD0E1',
    has_phase: '#FFE066',
};

// --- compare.html lines 5643-5687: LLM_FAILURE_COPY + renderSlotFailure ---
const LLM_FAILURE_COPY = {
    'context_exceeded': {
        title: 'Failed to generate',
        help: 'This document is larger than the model\'s context window. That\'s the point of this pipeline — a single-shot LLM call can\'t always fit the whole input. The deterministic pipelines on this page handle arbitrarily large documents because they work chunk-local without a global token budget.',
    },
    'quota_exceeded': {
        title: 'Failed to generate',
        help: 'The LLM provider rate-limited or ran out of quota. Wait a minute and retry, or switch to a different model tier in the dropdown.',
    },
    'output_truncated': {
        title: 'Failed to generate',
        help: 'The model hit its output token cap mid-response. The JSON was truncated and could not be parsed — typical on dense documents where the extraction output exceeds the model\'s output budget.',
    },
    'safety_block': {
        title: 'Failed to generate',
        help: 'The model\'s safety filters blocked the response. Try a different document or a different model.',
    },
    'backend_unreachable': {
        title: 'Failed to generate',
        help: 'The LLM provider timed out or was unreachable. Check network + try again.',
    },
    'extraction_failed': {
        title: 'Failed to generate',
        help: '',
    },
};

function renderSlotFailure(slotIdx, reason, message, errorType) {
    // Render the red "Failed to generate" overlay inside a slot's graph
    // container. Used by both the live SSE error handler and the
    // cached-replay path so failed runs look identical either way.
    const container = document.getElementById(`slot-${slotIdx}-graph`);
    if (!container) return;
    const copy = LLM_FAILURE_COPY[reason] || LLM_FAILURE_COPY.extraction_failed;
    const helpHtml = copy.help ? `<div style="margin-top:12px; color:#c9d1d9; font-size:12px;">${copy.help}</div>` : '';
    const errorTypeHtml = errorType ? `<div style="margin-top:6px; color:#888; font-size:10px; font-family:monospace;">error_type: ${errorType}</div>` : '';
    container.innerHTML = `
        <div class="placeholder" style="color:#E74C3C; padding:24px; text-align:left;">
            <div style="font-weight:700; margin-bottom:10px; font-size:16px;">${copy.title}</div>
            <div style="color:#c9d1d9; font-size:12px; font-family:monospace; background:#0a0a1a; padding:8px; border-radius:4px; border-left:3px solid #E74C3C;">${(message || 'Unknown error').replace(/</g, '&lt;')}</div>
            ${helpHtml}
            ${errorTypeHtml}
        </div>`;
}


// --- compare.html lines 5688-6126: renderGraph + graph toolbar + detail panels ---
function renderGraph(pipeline, visData, stats) {
    const containerId = `${pipeline}-graph`;
    const container = document.getElementById(containerId);
    container.innerHTML = '';

    if (!visData || !visData.nodes || visData.nodes.length === 0) {
        container.innerHTML = '<div class="placeholder">No relationships found</div>';
        return;
    }

    const nodes = new vis.DataSet(visData.nodes);
    const edges = new vis.DataSet(visData.edges);
    edgeDataSets[pipeline] = edges;
    nodeDataSets[pipeline] = nodes;
    // Sprint 39.3: Store metadata separately as fallback (vis.js may strip custom props)
    const nMeta = {};
    visData.nodes.forEach(n => { if (n.metadata) nMeta[n.id] = n.metadata; });
    nodeMetaMaps[pipeline] = nMeta;
    const eMeta = {};
    visData.edges.forEach(e => { if (e.metadata) eMeta[e.id] = e.metadata; });
    edgeMetaMaps[pipeline] = eMeta;

    // Sprint 33 (Item 2): Adaptive physics for large graphs (100+ nodes/edges)
    const nodeCount = visData.nodes.length;
    const edgeCount = visData.edges.length;
    const isLargeGraph = nodeCount > 60 || edgeCount > 80;
    const isHugeGraph = nodeCount > 120 || edgeCount > 150;

    const options = {
        layout: {
            randomSeed: 42,
        },
        physics: {
            barnesHut: {
                gravitationalConstant: isHugeGraph ? -1500 : isLargeGraph ? -2000 : -3000,
                centralGravity: isHugeGraph ? 0.5 : 0.3,
                springLength: isHugeGraph ? 180 : isLargeGraph ? 150 : 120,
                springConstant: 0.04,
                damping: isLargeGraph ? 0.15 : 0.09,
            },
            stabilization: { iterations: isHugeGraph ? 80 : isLargeGraph ? 100 : 150 },
            maxVelocity: isLargeGraph ? 30 : 50,
        },
        interaction: {
            hover: true,
            tooltipDelay: 100,
            navigationButtons: false,
        },
        edges: { smooth: { type: 'continuous' } },
        nodes: { shape: 'dot', borderWidth: 2, shadow: !isHugeGraph },
    };

    const network = new vis.Network(container, { nodes, edges }, options);
    networks[pipeline] = network;
    physicsEnabled[pipeline] = true;
    highlightedRel[pipeline] = null;

    // Sprint 33 (Item 2): Auto-disable physics after stabilization for large graphs
    if (isLargeGraph) {
        network.once('stabilizationIterationsDone', () => {
            network.setOptions({ physics: { enabled: false } });
            physicsEnabled[pipeline] = false;
            const btn = document.getElementById(`${pipeline}-physics-btn`);
            if (btn) btn.classList.remove('active');
        });
    }

    // Click-to-explore event handlers
    network.on('selectNode', (params) => {
        if (params.nodes.length === 1) showNodeDetail(pipeline, params.nodes[0]);
    });
    network.on('selectEdge', (params) => {
        if (params.edges.length === 1 && params.nodes.length === 0) {
            showEdgeDetail(pipeline, params.edges[0]);
        }
    });
    network.on('deselectNode', () => closeDetailPanel());
    network.on('deselectEdge', () => closeDetailPanel());

    // Show toolbar
    const toolbar = document.getElementById(`${pipeline}-toolbar`);
    if (toolbar) toolbar.style.display = 'flex';

    // Update stats
    const statsEl = document.getElementById(`${pipeline}-stats`);
    if (statsEl && stats) {
        if (pipeline === 'modular' && stats.h_tokens !== undefined) {
            statsEl.innerHTML = `
                <div class="stat entities"><span class="stat-value">${stats.entities}</span><span class="stat-label">entities</span></div>
                <div class="stat rels"><span class="stat-value">${stats.relationships}</span><span class="stat-label">rels</span></div>
                <div class="stat tokens"><span class="stat-value">${(stats.h_tokens || 0).toLocaleString()}</span><span class="stat-label">H-tok</span></div>
                <div class="stat tokens"><span class="stat-value">${(stats.l_tokens || 0).toLocaleString()}</span><span class="stat-label">L-tok</span></div>
                <div class="stat time"><span class="stat-value">${(stats.duration_ms / 1000).toFixed(1)}s</span><span class="stat-label">time</span></div>
            `;
        } else {
            statsEl.innerHTML = `
                <div class="stat entities"><span class="stat-value">${stats.entities}</span><span class="stat-label">entities</span></div>
                <div class="stat rels"><span class="stat-value">${stats.relationships}</span><span class="stat-label">rels</span></div>
                <div class="stat tokens"><span class="stat-value">${stats.tokens.toLocaleString()}</span><span class="stat-label">tokens</span></div>
                <div class="stat time"><span class="stat-value">${(stats.duration_ms / 1000).toFixed(1)}s</span><span class="stat-label">time</span></div>
            `;
        }
    }

    // Build legends using shared builder (click-to-filter enabled)
    buildLegend(pipeline, visData);
}

// ============================================================
// Graph Control Functions
// ============================================================
function graphFit(pipeline) {
    const net = networks[pipeline];
    if (net) net.fit({ animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
}

function graphZoomIn(pipeline) {
    const net = networks[pipeline];
    if (net) {
        const scale = net.getScale();
        net.moveTo({ scale: scale * 1.4, animation: { duration: 200 } });
    }
}

function graphZoomOut(pipeline) {
    const net = networks[pipeline];
    if (net) {
        const scale = net.getScale();
        net.moveTo({ scale: scale / 1.4, animation: { duration: 200 } });
    }
}

function graphTogglePhysics(pipeline) {
    const net = networks[pipeline];
    if (!net) return;
    physicsEnabled[pipeline] = !physicsEnabled[pipeline];
    net.setOptions({ physics: { enabled: physicsEnabled[pipeline] } });
    const btn = document.getElementById(`${pipeline}-physics-btn`);
    if (btn) btn.classList.toggle('active', physicsEnabled[pipeline]);
}

// Sprint 33.15 (WI-4): Toggle disconnected entity visibility
function graphToggleDisconnected(pipeline) {
    const nodes = nodeDataSets[pipeline];
    const edges = edgeDataSets[pipeline];
    if (!nodes || !edges) return;
    showDisconnected[pipeline] = !showDisconnected[pipeline];
    const connectedIds = new Set();
    edges.forEach(e => { connectedIds.add(e.from); connectedIds.add(e.to); });
    nodes.forEach(node => {
        if (!connectedIds.has(node.id)) {
            nodes.update({ id: node.id, hidden: !showDisconnected[pipeline] });
        }
    });
    const btn = document.getElementById(`${pipeline}-disconnected-btn`);
    if (btn) btn.classList.toggle('active', showDisconnected[pipeline]);
}

// ============================================================
// Sprint 39.3: Graph Node Search
// ============================================================
const originalNodeColors = {}; // pipeline -> { nodeId: colorObj }

function graphSearch(pipeline, query) {
    const nodes = nodeDataSets[pipeline];
    const countEl = document.getElementById(`${pipeline}-search-count`);
    if (!nodes) return;

    // Restore original colors
    if (originalNodeColors[pipeline]) {
        const updates = [];
        for (const [id, color] of Object.entries(originalNodeColors[pipeline])) {
            updates.push({ id: parseInt(id) || id, color: color, borderWidth: 2 });
        }
        if (updates.length) nodes.update(updates);
    }
    originalNodeColors[pipeline] = {};

    if (!query || query.trim().length === 0) {
        if (countEl) countEl.textContent = '';
        return;
    }

    const lowerQuery = query.toLowerCase().trim();
    const allNodes = nodes.get();
    const matches = [];
    const metaMap = nodeMetaMaps[pipeline] || {};

    for (const node of allNodes) {
        const meta = node.metadata || metaMap[node.id];
        const label = (node.label || '').toLowerCase();
        const text = (meta && meta.text || '').toLowerCase();
        if (label.includes(lowerQuery) || text.includes(lowerQuery)) {
            originalNodeColors[pipeline][node.id] = node.color ? { ...node.color } : {};
            matches.push(node.id);
        }
    }

    if (countEl) {
        countEl.textContent = matches.length > 0 ? `${matches.length} found` : 'none';
        countEl.style.color = matches.length > 0 ? '#5B9FE6' : '#ff6b6b';
    }

    // Highlight matches with gold border
    if (matches.length > 0) {
        const updates = matches.map(id => ({
            id,
            borderWidth: 4,
            color: { ...(nodes.get(id).color || {}), border: '#FFD700', highlight: { border: '#FFD700' } },
        }));
        nodes.update(updates);
    }

    // If exactly one match, focus on it
    if (matches.length === 1) {
        const net = networks[pipeline];
        if (net) {
            net.focus(matches[0], { scale: 1.5, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
            net.selectNodes([matches[0]], false);
            showNodeDetail(pipeline, matches[0]);
        }
    }
}

// ============================================================
// Detail Panel — Click-to-Explore
// ============================================================
function showNodeDetail(pipeline, nodeId) {
    const nodes = nodeDataSets[pipeline];
    const edges = edgeDataSets[pipeline];
    if (!nodes || !edges) { console.warn('[showNodeDetail] missing dataset for', pipeline); return; }

    const node = nodes.get(nodeId);
    // Sprint 39.3: fallback to separate metadata map if vis.js stripped custom props
    const meta = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
    if (!node || !meta) { console.warn('[showNodeDetail] missing node/metadata:', pipeline, nodeId, node); return; }

    detailPipeline = pipeline;
    const color = TYPE_COLORS[meta.entity_type] || '#AAA';

    // Find connected edges
    const connEdges = edges.get().filter(e => e.from === nodeId || e.to === nodeId);

    let connectionsHtml = '';
    if (connEdges.length > 0) {
        const items = connEdges.map(e => {
            const isSource = e.from === nodeId;
            const otherNodeId = isSource ? e.to : e.from;
            const otherNode = nodes.get(otherNodeId);
            const otherName = otherNode ? (otherNode.metadata ? otherNode.metadata.text : otherNode.label) : '?';
            const arrow = isSource ? '&rarr;' : '&larr;';
            const fbKey = `${pipeline}_${e.id}`;
            const hasFeedback = feedbackState[fbKey];
            const flagStyle = hasFeedback
                ? `color:${hasFeedback.type === 'fp' ? '#ff6b6b' : '#d4a017'};opacity:1`
                : 'color:#666;opacity:0.5';
            return `<li style="display:flex;align-items:center;gap:6px;">
                <span onclick="navigateToEdge('${pipeline}', '${e.id}')" title="Click to view relationship" style="flex:1;cursor:pointer;">
                    <span class="rel-name">${e.label || '?'}</span> ${arrow} ${otherName}
                </span>
                <span onclick="event.stopPropagation();navigateToEdge('${pipeline}', '${e.id}')" title="Click to flag/save this relationship" style="cursor:pointer;font-size:14px;${flagStyle}">&#9873;</span>
            </li>`;
        });
        connectionsHtml = `
            <div class="detail-row">
                <div class="detail-label">Connections (${connEdges.length})</div>
                <ul class="detail-connected-list">${items.join('')}</ul>
            </div>`;
    }

    // Sprint 33 (VP R1): Global Identity badge
    let canonicalHtml = '';
    if (meta.canonical_id) {
        canonicalHtml = `
            <div class="detail-row">
                <div class="detail-label">Global Identity</div>
                <div class="detail-value" style="font-size:11px;color:#5B9FE6">${meta.canonical_id}</div>
            </div>`;
    }

    document.getElementById('detail-title').textContent = meta.text;
    document.getElementById('detail-body').innerHTML = `
        <div class="detail-row">
            <div class="detail-label">Entity Type</div>
            <div class="detail-type-badge" style="background:${color}22; color:${color}; border:1px solid ${color}44">${meta.entity_type}</div>
        </div>
        ${canonicalHtml}
        <div class="detail-row">
            <div class="detail-label">Confidence</div>
            <div class="detail-value">${(meta.confidence * 100).toFixed(0)}%</div>
        </div>
        <div class="detail-row">
            <div class="detail-label">Mentions</div>
            <div class="detail-value">${meta.mention_count}</div>
        </div>
        ${connectionsHtml}
        ${buildAutoFlagAlert(pipeline, 'node', nodeId)}
        ${buildEntityFeedbackButton(pipeline, nodeId)}
        ${buildEntityFNButton(pipeline, nodeId)}
    `;
    document.getElementById('detail-panel').classList.add('open');
    // Sprint 90: Also open Document Explorer for this node
    openDocExplorer(pipeline, nodeId);
}

function showEdgeDetail(pipeline, edgeId) {
    const edges = edgeDataSets[pipeline];
    const nodes = nodeDataSets[pipeline];
    if (!edges || !nodes) { console.warn('[showEdgeDetail] missing dataset for', pipeline); return; }

    const edge = edges.get(edgeId);
    // Sprint 39.3: fallback to separate metadata map
    const meta = (edge && edge.metadata) || (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][edgeId]);
    if (!edge || !meta) { console.warn('[showEdgeDetail] missing edge/metadata:', pipeline, edgeId, edge); return; }

    detailPipeline = pipeline;
    const color = REL_COLORS[meta.predicate] || '#AAA';

    const subjNode = nodes.get(meta.subject_id);
    const objNode = nodes.get(meta.object_id);
    const subjName = meta.subject_text || (subjNode ? subjNode.label : '?');
    const objName = meta.object_text || (objNode ? objNode.label : '?');

    // Sprint 33 (VP R1): Global Identity badges from canonical_id
    const subjCanonical = subjNode?.metadata?.canonical_id;
    const objCanonical = objNode?.metadata?.canonical_id;
    const canonBadge = (id) => id
        ? ` <span style="font-size:10px;color:#5B9FE6;opacity:0.7">(Global: ${id})</span>`
        : '';

    let evidenceHtml = '';
    if (meta.evidence_text) {
        let evidenceContent = meta.evidence_text;
        // Sprint 39 D3: Show aggregated evidence from merged duplicate triples
        if (meta.additional_evidence_texts && meta.additional_evidence_texts.length > 0) {
            evidenceContent += '<br><br><strong>Additional evidence:</strong>';
            meta.additional_evidence_texts.forEach(t => {
                evidenceContent += '<br><br>' + t;
            });
        }
        evidenceHtml = `
            <div class="detail-row">
                <div class="detail-label">Evidence</div>
                <div class="detail-evidence">${evidenceContent}</div>
            </div>`;
    }

    // Sprint 33 (Item 3): Extraction source display
    let sourceHtml = '';
    if (meta.extraction_method) {
        const sourceLabels = {
            'table_extraction': 'Structural Table',
            'semantic_fingerprint': 'Semantic Fingerprint',
            'discovery_head': 'Discovery Head (SVO)',
            'anchor_head': 'Anchor Head',
            'pairwise_glirel': 'Pairwise GLiREL',
            'pairwise_glirel_cross_chunk': 'Cross-Chunk GLiREL',
        };
        const sourceLabel = sourceLabels[meta.extraction_method] || meta.extraction_method;
        const isTable = meta.extraction_method === 'table_extraction';
        const badgeColor = isTable ? '#C49A6C' : color;
        sourceHtml = `
            <div class="detail-row">
                <div class="detail-label">Source</div>
                <div class="detail-type-badge" style="background:${badgeColor}22; color:${badgeColor}; border:1px solid ${badgeColor}44">
                    ${isTable ? '&#128203; ' : ''}${sourceLabel}
                </div>
            </div>`;
    }

    // Build metadata display (amount, count, etc.)
    let metadataHtml = '';
    if (meta.rel_metadata && Object.keys(meta.rel_metadata).length > 0) {
        const items = Object.entries(meta.rel_metadata).map(([k, v]) =>
            `<div class="detail-value"><strong>${k}:</strong> ${v}</div>`
        ).join('');
        metadataHtml = `
            <div class="detail-row">
                <div class="detail-label">Properties</div>
                ${items}
            </div>`;
    }

    document.getElementById('detail-title').textContent = meta.predicate;
    document.getElementById('detail-body').innerHTML = `
        <div class="detail-row">
            <div class="detail-label">Relationship</div>
            <div class="detail-type-badge" style="background:${color}22; color:${color}; border:1px solid ${color}44">${meta.predicate}</div>
        </div>
        <div class="detail-row">
            <div class="detail-label">Confidence</div>
            <div class="detail-value">${(meta.confidence * 100).toFixed(0)}%</div>
        </div>
        ${sourceHtml}
        ${metadataHtml}
        <div class="detail-row">
            <div class="detail-label">Subject</div>
            <ul class="detail-connected-list">
                <li onclick="navigateToNode('${pipeline}', ${meta.subject_id})" title="Click to view entity">${subjName}${canonBadge(subjCanonical)}</li>
            </ul>
        </div>
        <div class="detail-row">
            <div class="detail-label">Object</div>
            <ul class="detail-connected-list">
                <li onclick="navigateToNode('${pipeline}', ${meta.object_id})" title="Click to view entity">${objName}${canonBadge(objCanonical)}</li>
            </ul>
        </div>
        ${evidenceHtml}
        ${buildAutoFlagAlert(pipeline, 'edge', edgeId)}
        ${buildFeedbackButton(pipeline, edgeId)}
    `;
    document.getElementById('detail-panel').classList.add('open');
}

function closeDetailPanel() {
    document.getElementById('detail-panel').classList.remove('open');
    detailPipeline = null;
}

function navigateToNode(pipeline, nodeId) {
    const net = networks[pipeline];
    if (net) {
        net.selectNodes([nodeId], false);
        net.focus(nodeId, { scale: 1.2, animation: { duration: 400, easingFunction: 'easeInOutQuad' } });
    }
    showNodeDetail(pipeline, nodeId);
}

function navigateToEdge(pipeline, edgeId) {
    const net = networks[pipeline];
    if (net) {
        net.selectEdges([edgeId]);
    }
    showEdgeDetail(pipeline, edgeId);
}

// ============================================================
// PRD-039: Comparison Matrix
// ============================================================

// --- compare.html lines 9574-9634: openDocExplorer + closeDocExplorer ---
function openDocExplorer(pipeline, nodeId) {
    const nodes = nodeDataSets[pipeline];
    if (!nodes) return;
    const node = nodes.get(nodeId);
    const meta = (node && node.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][nodeId]);
    if (!meta) return;

    const pipLabels = {kgenskills: 'KGSpin', modular: 'LLM Multi-Stage', gemini: 'LLM Full Shot', intelligence: 'Intelligence'};
    document.getElementById('doc-explorer-title').textContent = `Evidence for '${meta.text}'`;
    document.getElementById('doc-explorer-subtitle').textContent = `${pipLabels[pipeline] || pipeline} Pipeline`;

    // Find all edges connected to this node for evidence sentences
    const edges = edgeDataSets[pipeline];
    const connEdges = edges ? edges.get().filter(e => e.from === nodeId || e.to === nodeId) : [];

    let html = '';

    // Entity-level evidence
    if (meta.sources && meta.sources.length > 0) {
        html += `<div style="margin-bottom:12px;">
            <div style="color:#5B9FE6; font-size:12px; font-weight:600; margin-bottom:6px;">Sources</div>
            ${meta.sources.map(s => `<div style="color:#888; font-size:11px; padding:2px 0;">${s}</div>`).join('')}
        </div>`;
    }

    // Edge evidence
    if (connEdges.length > 0) {
        html += `<div style="color:#5ED68A; font-size:12px; font-weight:600; margin-bottom:6px;">Relationship Evidence (${connEdges.length})</div>`;
        connEdges.forEach(e => {
            const eMeta = (e.metadata) || (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][e.id]);
            if (!eMeta) return;
            const evidenceText = eMeta.full_evidence_text || eMeta.evidence_text || '';
            const otherNodeId = e.from === nodeId ? e.to : e.from;
            const otherNode = nodes.get(otherNodeId);
            const otherName = otherNode ? (otherNode.metadata ? otherNode.metadata.text : otherNode.label) : '?';
            const direction = e.from === nodeId ? '→' : '←';

            html += `<div style="background:#1a1a3e; border:1px solid #2a2a4e; border-radius:6px; padding:10px; margin-bottom:8px;">
                <div style="color:#ccc; font-size:12px; font-weight:600;">${eMeta.predicate} ${direction} ${otherName}</div>
                <div style="color:#888; font-size:11px; margin-top:4px;">Confidence: ${(eMeta.confidence * 100).toFixed(0)}%</div>
                ${evidenceText ? `<div style="color:#aaa; font-size:11px; margin-top:6px; padding:6px 8px; background:#0f0f23; border-radius:4px; font-style:italic;">"${evidenceText}"</div>` : ''}
                ${eMeta.additional_evidence_texts && eMeta.additional_evidence_texts.length > 0
                    ? `<div style="color:#666; font-size:10px; margin-top:4px;">+${eMeta.additional_evidence_texts.length} additional evidence sentences</div>`
                    : ''}
            </div>`;
        });
    }

    if (!html) {
        html = '<div style="color:#666;">No evidence data available for this entity.</div>';
    }

    document.getElementById('doc-explorer-content').innerHTML = html;
    document.getElementById('doc-explorer-modal').style.display = 'block';
}

function closeDocExplorer() {
    document.getElementById('doc-explorer-modal').style.display = 'none';
}

// --- Agentic Q&A ---

// --- compare.html lines 9674-9842: Legend + buildLegend + getConfidenceFloor + IIFE badge ---
const legendFilters = {};  // pipeline -> { entityTypes: Set, relTypes: Set }

function _getLegendFilter(pipeline) {
    if (!legendFilters[pipeline]) legendFilters[pipeline] = { entityTypes: new Set(), relTypes: new Set() };
    return legendFilters[pipeline];
}

// Determine which pipelines share filters (slot panels sync with each other, modal graphs are independent)
function _filterGroup(pipeline) {
    if (pipeline.startsWith('modal-')) return [pipeline];
    // Slot panels sync filters across all small-panel graphs
    return Object.keys(nodeDataSets).filter(k => !k.startsWith('modal-'));
}

function toggleEntityTypeFilter(pipeline, entityType) {
    const group = _filterGroup(pipeline);
    for (const p of group) {
        const f = _getLegendFilter(p);
        if (f.entityTypes.has(entityType)) f.entityTypes.delete(entityType);
        else f.entityTypes.add(entityType);
        applyLegendFilters(p);
    }
}

function toggleRelHighlight(pipeline, relType) {
    const group = _filterGroup(pipeline);
    for (const p of group) {
        const f = _getLegendFilter(p);
        if (f.relTypes.has(relType)) f.relTypes.delete(relType);
        else f.relTypes.add(relType);
        applyLegendFilters(p);
    }
}

function applyLegendFilters(pipeline) {
    const f = _getLegendFilter(pipeline);
    const hasEntityFilter = f.entityTypes.size > 0;
    const hasRelFilter = f.relTypes.size > 0;

    const nodes = nodeDataSets[pipeline];
    const edges = edgeDataSets[pipeline];
    if (!nodes || !edges) return;

    const allEdges = edges.get();
    const allNodes = nodes.get();
    const visibleEdgeFromTo = new Set();

    allEdges.forEach(e => {
        const eMeta = (e.metadata) || (edgeMetaMaps[pipeline] && edgeMetaMaps[pipeline][e.id]);
        const relType = eMeta ? eMeta.predicate : (e.label || '');
        const relVisible = !hasRelFilter || f.relTypes.has(relType);
        if (relVisible) { visibleEdgeFromTo.add(e.from); visibleEdgeFromTo.add(e.to); }
        edges.update({id: e.id, hidden: !relVisible});
    });

    allNodes.forEach(n => {
        const nMeta = (n.metadata) || (nodeMetaMaps[pipeline] && nodeMetaMaps[pipeline][n.id]);
        const entityType = nMeta ? nMeta.entity_type : '';
        let visible = true;
        if (hasEntityFilter) {
            const typeMatch = f.entityTypes.has(entityType);
            const connectedToMatch = visibleEdgeFromTo.has(n.id);
            visible = typeMatch || (hasRelFilter && connectedToMatch);
        }
        if (hasRelFilter && !hasEntityFilter) {
            visible = visibleEdgeFromTo.has(n.id);
        }
        nodes.update({id: n.id, hidden: !visible});
    });

    updateLegendActiveStates(pipeline);
}

function updateLegendActiveStates(pipeline) {
    const f = _getLegendFilter(pipeline);
    const legendEl = document.getElementById(`${pipeline}-legend`);
    if (legendEl) {
        legendEl.querySelectorAll('.legend-item').forEach(item => {
            const type = item.dataset.type;
            if (type) item.style.opacity = f.entityTypes.size === 0 || f.entityTypes.has(type) ? '1' : '0.3';
        });
    }
    const relLegendEl = document.getElementById(`${pipeline}-rel-legend`);
    if (relLegendEl) {
        relLegendEl.querySelectorAll('.rel-legend-item').forEach(item => {
            const rel = item.dataset.rel;
            if (rel) item.style.opacity = f.relTypes.size === 0 || f.relTypes.has(rel) ? '1' : '0.3';
        });
    }
}

function clearLegendFilters(pipeline) {
    if (pipeline) {
        const f = _getLegendFilter(pipeline);
        f.entityTypes.clear();
        f.relTypes.clear();
        applyLegendFilters(pipeline);
        const nodes = nodeDataSets[pipeline];
        const edges = edgeDataSets[pipeline];
        if (nodes) nodes.get().forEach(n => nodes.update({id: n.id, hidden: false}));
        if (edges) edges.get().forEach(e => edges.update({id: e.id, hidden: false}));
    } else {
        // Clear all
        for (const p of Object.keys(legendFilters)) {
            clearLegendFilters(p);
        }
    }
}

// Shared legend builder — works for both slot panels and modal graphs
function buildLegend(pipeline, visData) {
    const typeNoiseMap = {};
    visData.nodes.forEach(n => {
        const meta = n.metadata || {};
        const t = meta.entity_type;
        if (t && !(t in typeNoiseMap)) typeNoiseMap[t] = !!meta.is_noise;
    });
    const legendEl = document.getElementById(`${pipeline}-legend`);
    if (legendEl) {
        const sortedTypes = Object.keys(typeNoiseMap).sort((a, b) =>
            (typeNoiseMap[a] ? 1 : 0) - (typeNoiseMap[b] ? 1 : 0) || a.localeCompare(b)
        );
        legendEl.innerHTML = sortedTypes.map(t => {
            const isNoise = typeNoiseMap[t];
            const color = TYPE_COLORS[t] || '#AAA';
            const style = isNoise ? `background:${color}; border: 2px solid #FF4444;` : `background:${color};`;
            const label = isNoise ? `<span style="opacity:0.5">${t}</span>` : t;
            return `<div class="legend-item" data-type="${t}" onclick="toggleEntityTypeFilter('${pipeline}','${t}')" style="cursor:pointer;"><div class="legend-dot" style="${style}"></div>${label}</div>`;
        }).join('');
    }

    const relTypes = new Set();
    visData.edges.forEach(e => { if (e.label) relTypes.add(e.label); });
    const relLegendEl = document.getElementById(`${pipeline}-rel-legend`);
    if (relLegendEl && relTypes.size > 0) {
        relLegendEl.innerHTML = Array.from(relTypes).map(rt =>
            `<div class="legend-item rel-legend-item" data-rel="${rt}" onclick="toggleRelHighlight('${pipeline}','${rt}')" style="cursor:pointer;">` +
            `<div class="legend-line" style="background:${REL_COLORS[rt] || '#AAA'}"></div>${rt}</div>`
        ).join('');
    }
    const hasStructural = visData.edges.some(e =>
        e.metadata && e.metadata.extraction_method === 'table_extraction'
    );
    if (relLegendEl && hasStructural) {
        const el = document.createElement('div');
        el.className = 'legend-item';
        el.innerHTML = '<div class="legend-line" style="background:#C49A6C; border-top:2px dashed #C49A6C; height:0"></div>Structural Table';
        relLegendEl.appendChild(el);
    }
}

// --- Confidence Floor URL Param (Sprint 90 Task 10) ---
function getConfidenceFloor() {
    const params = new URLSearchParams(window.location.search);
    const cf = parseFloat(params.get('confidence_floor'));
    if (!isNaN(cf) && cf >= 0.0 && cf <= 1.0) return cf;
    return 0.55; // default
}

(function showConfidenceFloorBadge() {
    const cf = getConfidenceFloor();
    if (cf !== 0.55) {
        const badge = document.createElement('div');
        badge.style.cssText = 'position:fixed; bottom:10px; right:10px; background:#2a1a3a; border:1px solid #5a3a7a; color:#E088E5; padding:6px 12px; border-radius:6px; font-size:11px; z-index:100;';
        badge.textContent = `Confidence Floor: ${cf.toFixed(2)} (Custom)`;
        document.body.appendChild(badge);
    }
})();

// Wave E — graph.js action registrations
registerAction('graph-fit', (el) => graphFit(el.dataset.graphId));
registerAction('graph-zoom-in', (el) => graphZoomIn(el.dataset.graphId));
registerAction('graph-zoom-out', (el) => graphZoomOut(el.dataset.graphId));
registerAction('graph-toggle-physics', (el) => graphTogglePhysics(el.dataset.graphId));
registerAction('graph-toggle-disconnected', (el) => graphToggleDisconnected(el.dataset.graphId));
registerAction('graph-search', (el) => graphSearch(el.dataset.graphId, el.value));

registerAction('close-detail-panel', () => closeDetailPanel());
registerAction('close-doc-explorer', () => closeDocExplorer());

// HITL edge modals
registerAction('close-fp-modal', () => closeFPModal());
registerAction('submit-false-positive', () => submitFalsePositive());
registerAction('update-fp-submit-state', () => updateFPSubmitState());
registerAction('close-fn-modal', () => closeFNModal());
registerAction('submit-false-negative', () => submitFalseNegative());
registerAction('open-doc-viewer', () => openDocViewer());

// HITL entity modals
registerAction('close-entity-fp-modal', () => closeEntityFPModal());
registerAction('submit-entity-fp', () => submitEntityFP());
registerAction('update-entity-fp-submit-state', () => updateEntityFPSubmitState());
registerAction('close-entity-fn-modal', () => closeEntityFNModal());
registerAction('submit-entity-fn', () => submitEntityFN());

// Doc viewer
registerAction('close-doc-viewer', () => closeDocViewer());
registerAction('doc-search-debounced', () => docSearchDebounced());
registerAction('doc-search-prev', () => docSearchPrev());
registerAction('doc-search-next', () => docSearchNext());
registerAction('confirm-doc-viewer-selection', () => confirmDocViewerSelection());


