"""Feedback endpoints (FP / FN / TP / retract / bulk_retract / list).

The auto-flag and auto-discover-TP LLM orchestrators stay in
``demo_compare`` for now — they're large SSE pipelines (~1,100 LOC
combined) that warrant their own Wave C carve.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _feedback_store():
    from demo_compare import _get_feedback_store
    return _get_feedback_store()


def _bundle():
    from demo_compare import _get_bundle
    return _get_bundle()


def _bundle_predicates():
    from demo_compare import _get_bundle_predicates
    return _get_bundle_predicates()


@router.post("/api/feedback/false_positive")
async def submit_false_positive(request: Request):
    """Store a user-flagged False Positive from the KGS graph."""
    from kgenskills.feedback.models import FalsePositiveFeedback

    body = await request.json()
    bundle = _bundle()
    fp = FalsePositiveFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", "kgenskills"),
        feedback_target=body.get("feedback_target", "relationship"),
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate=body.get("predicate", ""),
        object_text=body.get("object_text", ""),
        object_type=body.get("object_type", ""),
        confidence=float(body.get("confidence", 0)),
        evidence_sentence=body.get("evidence_sentence", ""),
        source_document=body.get("source_document", ""),
        chunk_id=body.get("chunk_id", ""),
        extraction_method=body.get("extraction_method", ""),
        reasons=body.get("reasons", []),
        reason_detail=body.get("reason_detail", ""),
        corrected_type=body.get("corrected_type", ""),
        resolve_to_entity=body.get("resolve_to_entity", ""),
        flagged_by=body.get("flagged_by", "user"),
    )
    store = _feedback_store()
    feedback_id = store.add_false_positive(fp)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@router.post("/api/feedback/false_negative")
async def submit_false_negative(request: Request):
    """Store a user-validated False Negative from the LLM graph.

    Validates predicate against bundle schema (VP Eng guardrail #2).
    """
    from kgenskills.feedback.models import FalseNegativeFeedback

    body = await request.json()
    feedback_target = body.get("feedback_target", "relationship")
    predicate = body.get("predicate", "")
    evidence_sentence = body.get("evidence_sentence", "")

    # Sprint 39.3: Entity-level FN skips predicate/evidence validation
    if feedback_target == "relationship":
        # Strict predicate validation against bundle schema (VP Eng guardrail #2)
        valid_predicates = {p["name"] for p in _bundle_predicates()}
        if predicate not in valid_predicates:
            return JSONResponse(
                {
                    "error": f"Invalid predicate '{predicate}'. Must be one of: {sorted(valid_predicates)}",
                    "valid_predicates": sorted(valid_predicates),
                },
                status_code=400,
            )

        # Zero Trust evidence validation (VP Eng guardrail #4)
        if len(evidence_sentence) < 10:
            return JSONResponse(
                {"error": "evidence_sentence must be at least 10 characters"},
                status_code=400,
            )
        if len(evidence_sentence) > 1000:
            return JSONResponse(
                {"error": "evidence_sentence must be at most 1000 characters"},
                status_code=400,
            )

    bundle = _bundle()
    fn = FalseNegativeFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", ""),
        feedback_target=feedback_target,
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate=predicate,
        object_text=body.get("object_text", ""),
        object_type=body.get("object_type", ""),
        evidence_sentence=evidence_sentence,
        source_document=body.get("source_document", ""),
        original_confidence=float(body.get("original_confidence", 0)),
        original_evidence=body.get("original_evidence", ""),
    )
    store = _feedback_store()
    feedback_id = store.add_false_negative(fn)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@router.post("/api/feedback/true_positive")
async def submit_true_positive(request: Request):
    """Store a user-confirmed True Positive from any graph panel (Sprint 90)."""
    from kgenskills.feedback.models import FalsePositiveFeedback

    body = await request.json()
    bundle = _bundle()
    fp = FalsePositiveFeedback(
        bundle_version=getattr(bundle, "version", "unknown"),
        document_id=body.get("document_id", ""),
        pipeline=body.get("pipeline", "kgenskills"),
        feedback_target="entity_tp",
        subject_text=body.get("subject_text", ""),
        subject_type=body.get("subject_type", ""),
        predicate="",
        object_text="",
        object_type="",
        confidence=float(body.get("confidence", 0)),
        evidence_sentence="",
        source_document="",
        chunk_id="",
        extraction_method="",
        reasons=["confirmed_tp"],
        reason_detail="",
        corrected_type="",
        flagged_by="user",
    )
    store = _feedback_store()
    feedback_id = store.add_false_positive(fp)
    return JSONResponse({"id": feedback_id, "status": "stored"})


@router.post("/api/feedback/retract")
async def retract_feedback(request: Request):
    """Soft-delete a feedback entry."""
    body = await request.json()
    feedback_id = body.get("feedback_id", "")
    if not feedback_id:
        return JSONResponse({"error": "feedback_id required"}, status_code=400)

    store = _feedback_store()
    retracted = store.retract(feedback_id)
    if retracted:
        return JSONResponse({"status": "retracted"})
    return JSONResponse({"status": "not_found"}, status_code=404)


@router.post("/api/feedback/bulk_retract")
async def bulk_retract_feedback(request: Request):
    """Bulk soft-delete feedback entries matching filters.

    Body params (all optional, acts as AND filter):
      - document_id: retract only for this document
      - feedback_type: "fp", "fn", or "tp" (default: all)
      - reason: retract only entries containing this reason code
    """
    body = await request.json()
    document_id = body.get("document_id")
    feedback_type = body.get("feedback_type")
    reason = body.get("reason")

    store = _feedback_store()
    count = store.bulk_retract(
        document_id=document_id,
        feedback_type=feedback_type,
        reason=reason,
    )
    logger.info(
        f"Bulk retract: {count} entries retracted "
        f"(document_id={document_id}, type={feedback_type}, reason={reason})"
    )
    return JSONResponse({"status": "ok", "retracted_count": count})


@router.get("/api/feedback/list")
async def list_feedback(request: Request):
    """Return all active (non-retracted) FP and FN entries from the feedback store.

    Sprint 48: Enables the Flag Explorer to show persisted feedback on app load,
    not just session-generated flags.
    """
    store = _feedback_store()
    document_id = request.query_params.get("document_id")
    fps = store.get_false_positives(document_id=document_id)
    fns = store.get_false_negatives(document_id=document_id)
    return JSONResponse({
        "false_positives": [fp.to_dict() for fp in fps],
        "false_negatives": [fn.to_dict() for fn in fns],
    })
