"""LLM-answer micro-graph builder (PRD-055 #3).

Deterministic NER + verb-extraction over an LLM answer, producing a
small kgspin-shaped dict KG. The builder is intentionally lightweight
(spaCy NER + dependency parse only — no LLM, no GLiREL) so it does not
recreate the kgspin pipeline. The asymmetry is the point: LLM answers
typically yield small, sparse graphs that score lower on topological
health than the corresponding KG-pipeline KGs.
"""
from __future__ import annotations

from threading import Lock

_NLP = None
_NLP_LOCK = Lock()
_MAX_NODES = 50


def _load_nlp():
    global _NLP
    if _NLP is None:
        with _NLP_LOCK:
            if _NLP is None:
                import spacy
                _NLP = spacy.load("en_core_web_sm", disable=["lemmatizer", "textcat"])
    return _NLP


def build_micrograph_from_answer(answer_text: str) -> dict:
    """Extract a tiny KG from an LLM answer.

    Entities = spaCy NER spans (deduped by lowercased text). Relations =
    SVO triples extracted from the dependency parse: any token whose
    ``dep_`` is ``ROOT`` and pos_ is ``VERB`` becomes a predicate, with
    the nominal subject and direct object as endpoints. Both endpoints
    must resolve to a known entity span — if either doesn't, the triple
    is dropped.

    Returns the dict shape that demo helpers (``build_vis_data``,
    ``_build_kg_context_string``, ``health_for_kg``) already accept.
    Capped at ``_MAX_NODES`` entities so a runaway long answer can't
    inflate the LLM-side score artificially.
    """
    if not answer_text or not answer_text.strip():
        return {"entities": [], "relationships": []}

    nlp = _load_nlp()
    doc = nlp(answer_text)

    seen_keys: set[str] = set()
    entities: list[dict] = []
    span_lookup: dict[str, dict] = {}

    for ent in doc.ents:
        key = ent.text.strip().lower()
        if not key or key in seen_keys:
            continue
        if len(entities) >= _MAX_NODES:
            break
        seen_keys.add(key)
        entity = {
            "text": ent.text.strip(),
            "entity_type": ent.label_ or "MISC",
            "confidence": 1.0,
        }
        entities.append(entity)
        span_lookup[key] = entity
        for tok in ent:
            span_lookup.setdefault(tok.text.lower(), entity)

    relationships: list[dict] = []
    for sent in doc.sents:
        for tok in sent:
            if tok.pos_ != "VERB":
                continue
            subj = _find_child(tok, ("nsubj", "nsubjpass"))
            obj = _find_child(tok, ("dobj", "attr", "pobj", "obj"))
            if not obj and tok.dep_ == "ROOT":
                obj = _find_prep_object(tok)
            if not subj or not obj:
                continue
            subj_ent = _resolve_entity(subj, span_lookup)
            obj_ent = _resolve_entity(obj, span_lookup)
            if not subj_ent or not obj_ent or subj_ent is obj_ent:
                continue
            relationships.append({
                "subject": {"text": subj_ent["text"], "entity_type": subj_ent["entity_type"]},
                "object": {"text": obj_ent["text"], "entity_type": obj_ent["entity_type"]},
                "predicate": tok.lemma_ or tok.text,
                "confidence": 1.0,
                "evidence": {"sentence_text": sent.text.strip()},
            })

    return {"entities": entities, "relationships": relationships}


def _find_child(token, deps: tuple[str, ...]):
    for child in token.children:
        if child.dep_ in deps:
            return child
    return None


def _find_prep_object(verb):
    for child in verb.children:
        if child.dep_ == "prep":
            for grand in child.children:
                if grand.dep_ in ("pobj", "obj"):
                    return grand
    return None


def _resolve_entity(token, span_lookup: dict[str, dict]):
    if token is None:
        return None
    if token.ent_type_:
        for tok in [token] + list(token.subtree):
            ent = span_lookup.get(tok.text.lower())
            if ent is not None:
                return ent
    return span_lookup.get(token.text.lower())
