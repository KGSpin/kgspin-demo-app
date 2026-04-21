"""
Entity Resolution Service — pluggable identity resolution for the extraction pipeline.

Separates two concerns that were previously conflated in .h_module.json files:
1. Entity discovery — what entities were found in THIS document (H-Module's job)
2. Entity registry — what entities exist in the world (this service's job)

The resolution service maintains a canonical registry of entities with aliases.
After the H-Module discovers raw entity mentions in a document, this service
resolves them to canonical IDs. The enriched entity inventory is then passed
to the L-Module for relationship extraction.

Phase 1: Exact alias matching with aggressive text normalization.
Phase 2+: Fuzzy string matching, embedding similarity via EmbeddingEngine.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RawEntity:
    """Entity mention as discovered by H-Module."""

    text: str
    entity_type: str
    confidence: float
    source_document: str


@dataclass
class ResolvedEntity:
    """Entity resolved to a canonical ID."""

    entity_id: str
    canonical_name: str
    entity_type: str
    domain_type: Optional[str]
    match_confidence: float  # 1.0 = exact alias, 0.85+ = embedding match
    match_method: str  # "exact_alias" | "fuzzy" | "embedding" | "new"


@dataclass
class CanonicalEntity:
    """Entity in the canonical registry."""

    entity_id: str
    canonical_name: str
    entity_type: str
    domain_type: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    provenance: str = "auto_discovered"  # "seed" | "auto_discovered"
    source_documents: List[str] = field(default_factory=list)
    confidence: float = 1.0
    embedding: Optional[List[float]] = None  # Phase 2+
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Drop embedding if None to keep JSON small
        if d["embedding"] is None:
            del d["embedding"]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CanonicalEntity:
        return cls(
            entity_id=data["entity_id"],
            canonical_name=data["canonical_name"],
            entity_type=data["entity_type"],
            domain_type=data.get("domain_type"),
            aliases=data.get("aliases", []),
            provenance=data.get("provenance", "auto_discovered"),
            source_documents=data.get("source_documents", []),
            confidence=data.get("confidence", 1.0),
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_raw(cls, raw: RawEntity) -> CanonicalEntity:
        """Create a canonical entity from a raw H-Module discovery."""
        entity_id = generate_canonical_id(raw.entity_type, raw.text)
        return cls(
            entity_id=entity_id,
            canonical_name=raw.text,
            entity_type=raw.entity_type,
            provenance="auto_discovered",
            source_documents=[raw.source_document],
            confidence=raw.confidence,
            aliases=[],
        )


# ---------------------------------------------------------------------------
# Text normalization & ID generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Module-level normalization token config (Sprint 117 B-1 fix)
# ---------------------------------------------------------------------------

# Active admission tokens used by normalize_entity_text(). Set once per
# extraction run via configure_normalization_tokens(). When None, the legacy
# fallback regex is used (backward compat for standalone registry usage).
_active_admission_tokens: Optional[List[str]] = None
_active_admission_pattern: Optional[re.Pattern] = None


def configure_normalization_tokens(admission_gate_configs: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Set module-level normalization tokens from bundle admission_gate configs.

    Called once by the extractor when the bundle is loaded. All subsequent
    calls to normalize_entity_text() use these tokens instead of the
    hardcoded legacy fallback.

    Args:
        admission_gate_configs: Dict of type_name → gate config from
            TypeRegistry.get_admission_gate_types(), or None to reset.
    """
    global _active_admission_tokens, _active_admission_pattern
    if not admission_gate_configs:
        _active_admission_tokens = None
        _active_admission_pattern = None
        return
    # Merge all anchors from all gate configs into one deduplicated list
    tokens: List[str] = []
    seen: set = set()
    for gate in admission_gate_configs.values():
        for anchor in gate.get("anchors", []):
            key = anchor.lower()
            if key not in seen:
                tokens.append(key)
                seen.add(key)
    if not tokens:
        _active_admission_tokens = None
        _active_admission_pattern = None
        return
    _active_admission_tokens = tokens
    _active_admission_pattern = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in tokens) + r")\b\.?",
        re.IGNORECASE,
    )
    logger.debug(
        "[NORMALIZATION] Configured %d admission tokens: %s",
        len(tokens), tokens[:5],
    )


def build_normalization_tokens(admission_gate_config: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Build normalization token list from a single admission_gate config.

    Sprint 117: Reads anchors from the admission_gate and returns them
    as lowercase tokens for use in normalize_entity_text().

    Args:
        admission_gate_config: Dict with 'anchors' key, or None.

    Returns:
        List of lowercase token strings, or None if no gate provided.
    """
    if not admission_gate_config:
        return None
    anchors = admission_gate_config.get("anchors", [])
    if not anchors:
        return None
    return [a.lower() for a in anchors]


def normalize_entity_text(text: str, admission_tokens: Optional[List[str]] = None) -> str:
    """Aggressive normalization to prevent ID fragmentation.

    "Boston Scientific Corp." and "Boston Scientific" both normalize to
    "boston scientific", yielding the same canonical ID.

    Token matching is position-independent (uses \\b word boundaries),
    not restricted to suffixes.

    Args:
        text: Entity text to normalize.
        admission_tokens: Optional list of lowercase tokens to strip.
            When provided, uses the given list directly.
            When None, uses module-level _active_admission_tokens if
            configured via configure_normalization_tokens().
            Falls back to legacy hardcoded list only if neither is set.
    """
    text = text.lower()
    if admission_tokens is not None:
        pattern = r"\b(" + "|".join(re.escape(t) for t in admission_tokens) + r")\b\.?"
        text = re.sub(pattern, "", text)
    elif _active_admission_pattern is not None:
        text = _active_admission_pattern.sub("", text)
    else:
        # Legacy fallback for standalone registry usage (no bundle loaded)
        text = re.sub(r"\b(inc|corp|corporation|ltd|llc|plc|ag|gmbh|co|company|group|the)\b\.?", "", text)
    # Remove punctuation (keep & for entities like "Johnson & Johnson")
    text = re.sub(r"[^\w\s&]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_canonical_id(entity_type: str, text: str) -> str:
    """Deterministic ID from normalized text + type prefix.

    Examples:
        ("ORGANIZATION", "Boston Scientific Corp.") → "org-boston-scientific-a1b2c3"
        ("PERSON", "Lisa Su") → "per-lisa-su-d4e5f6"
    """
    clean = normalize_entity_text(text)
    slug = clean.replace(" ", "-")[:40]
    hash_suffix = hashlib.md5(clean.encode()).hexdigest()[:6]
    type_prefix = entity_type.lower()[:3]
    return f"{type_prefix}-{slug}-{hash_suffix}"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EntityResolutionService(Protocol):
    """Pluggable entity resolution — customers provide their own implementation."""

    def resolve(
        self,
        text: str,
        entity_type: str,
        context_text: Optional[str] = None,
    ) -> Optional[ResolvedEntity]:
        """Resolve entity mention to canonical entity. Returns None if no match."""
        ...

    def resolve_batch(
        self, entities: List[RawEntity]
    ) -> List[Optional[ResolvedEntity]]:
        """Batch resolution for efficiency."""
        ...

    def register(self, entity: CanonicalEntity) -> str:
        """Register new canonical entity. Returns entity_id.
        Idempotent — if normalized ID exists, merges aliases."""
        ...

    def register_alias(self, entity_id: str, alias: str) -> None:
        """Add alias to existing canonical entity."""
        ...

    def get(self, entity_id: str) -> Optional[CanonicalEntity]:
        """Look up by canonical ID."""
        ...

    def search(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[CanonicalEntity]:
        """Search for entities matching query."""
        ...


# ---------------------------------------------------------------------------
# JSONFileEntityService implementation
# ---------------------------------------------------------------------------


class JSONFileEntityService:
    """Entity resolution backed by a JSON file on disk.

    Phase 1 implementation: exact alias matching with aggressive normalization.
    Persists the canonical registry between runs. Uses file locking to prevent
    data loss from concurrent processes.
    """

    def __init__(self, registry_path: Path):
        self._path = Path(registry_path)
        self._entities: Dict[str, CanonicalEntity] = {}
        self._alias_map: Dict[str, str] = {}  # normalized_alias → entity_id
        self._main_entity: Optional[str] = None

        if self._path.exists():
            self.load()

    # --- Core resolution ---

    def resolve(
        self,
        text: str,
        entity_type: str,
        context_text: Optional[str] = None,
    ) -> Optional[ResolvedEntity]:
        """Phase 1: exact alias match after normalization."""
        normalized = normalize_entity_text(text)
        entity_id = self._alias_map.get(normalized)
        if entity_id is None:
            return None

        entity = self._entities[entity_id]
        return ResolvedEntity(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            domain_type=entity.domain_type,
            match_confidence=1.0,
            match_method="exact_alias",
        )

    def resolve_batch(
        self, entities: List[RawEntity]
    ) -> List[Optional[ResolvedEntity]]:
        return [self.resolve(e.text, e.entity_type) for e in entities]

    # --- Registration ---

    def register(self, entity: CanonicalEntity) -> str:
        """Register a canonical entity. Idempotent: merges aliases if ID exists."""
        # Generate ID from normalized text if not set
        if not entity.entity_id:
            entity.entity_id = generate_canonical_id(
                entity.entity_type, entity.canonical_name
            )

        existing = self._entities.get(entity.entity_id)
        if existing:
            # Merge aliases and source documents
            for alias in entity.aliases:
                norm_alias = normalize_entity_text(alias)
                if norm_alias and norm_alias not in self._alias_map:
                    existing.aliases.append(alias)
                    self._alias_map[norm_alias] = existing.entity_id
            for doc in entity.source_documents:
                if doc not in existing.source_documents:
                    existing.source_documents.append(doc)
            self.save()
            return existing.entity_id

        # New entity: register canonical name + all aliases
        self._entities[entity.entity_id] = entity
        # Register canonical name as alias
        norm_canonical = normalize_entity_text(entity.canonical_name)
        if norm_canonical:
            self._alias_map[norm_canonical] = entity.entity_id
        # Register explicit aliases
        for alias in entity.aliases:
            norm_alias = normalize_entity_text(alias)
            if norm_alias and norm_alias not in self._alias_map:
                self._alias_map[norm_alias] = entity.entity_id

        self.save()
        return entity.entity_id

    def register_alias(self, entity_id: str, alias: str) -> None:
        """Add an alias to an existing canonical entity."""
        entity = self._entities.get(entity_id)
        if not entity:
            return

        norm_alias = normalize_entity_text(alias)
        if norm_alias and norm_alias not in self._alias_map:
            entity.aliases.append(alias)
            self._alias_map[norm_alias] = entity_id
            self.save()

    # --- Lookup ---

    def get(self, entity_id: str) -> Optional[CanonicalEntity]:
        return self._entities.get(entity_id)

    def search(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[CanonicalEntity]:
        """Simple substring search (Phase 1)."""
        norm_query = normalize_entity_text(query)
        results = []
        for entity in self._entities.values():
            if entity_type and entity.entity_type != entity_type:
                continue
            norm_name = normalize_entity_text(entity.canonical_name)
            if norm_query in norm_name:
                results.append(entity)
                if len(results) >= limit:
                    break
        return results

    # --- Utility ---

    def entity_count(self) -> int:
        return len(self._entities)

    def get_main_entity(self) -> Optional[str]:
        return self._main_entity

    def get_all_entities(self) -> List[CanonicalEntity]:
        return list(self._entities.values())

    def get_entities_by_provenance(self, provenance: str) -> List[CanonicalEntity]:
        return [e for e in self._entities.values() if e.provenance == provenance]

    # --- Bootstrap ---

    def bootstrap_from_h_module(self, h_module_path: Path) -> int:
        """Load a .h_module.json and register all entities as seed (canonical).

        Idempotent — skips entities already in the registry.
        Returns the number of new entities registered.
        """
        with open(h_module_path) as f:
            data = json.load(f)

        self._main_entity = data.get("main_entity")
        count = 0

        for ent in data.get("entities", []):
            aliases = []
            for alias in ent.get("aliases", []):
                alias_text = (
                    alias.get("identifier", alias) if isinstance(alias, dict) else alias
                )
                if alias_text and len(alias_text) > 1:
                    aliases.append(alias_text)

            canonical = CanonicalEntity(
                entity_id=generate_canonical_id(ent["entity_type"], ent["text"]),
                canonical_name=ent["text"],
                entity_type=ent["entity_type"],
                domain_type=ent.get("domain_type"),
                aliases=aliases,
                provenance="seed",
                confidence=ent.get("confidence", 1.0),
                source_documents=[str(h_module_path)],
            )

            # Check if already registered (idempotent)
            if canonical.entity_id not in self._entities:
                self.register(canonical)
                count += 1
            else:
                # Still merge any new aliases
                existing = self._entities[canonical.entity_id]
                for alias in aliases:
                    norm_alias = normalize_entity_text(alias)
                    if norm_alias and norm_alias not in self._alias_map:
                        existing.aliases.append(alias)
                        self._alias_map[norm_alias] = existing.entity_id

        # Also register coreference entries as aliases for the main entity
        if self._main_entity:
            main_id = generate_canonical_id("ORGANIZATION", self._main_entity)
            for token, target in data.get("coreference_map", {}).items():
                # Only register non-pronoun aliases (skip "we", "our", etc.)
                if len(token) > 3 and token[0].isupper():
                    self.register_alias(main_id, token)

        self.save()
        return count

    # --- Persistence ---

    def save(self) -> None:
        """Save registry to disk with file locking + atomic write."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "main_entity": self._main_entity,
            "entity_count": len(self._entities),
            "entities": {
                eid: e.to_dict() for eid, e in self._entities.items()
            },
        }

        # Atomic write: write to temp file, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(data, f, indent=2)
                fcntl.flock(f, fcntl.LOCK_UN)
            os.rename(tmp_path, str(self._path))
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self) -> None:
        """Load registry from disk."""
        with open(self._path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            data = json.load(f)
            fcntl.flock(f, fcntl.LOCK_UN)

        self._main_entity = data.get("main_entity")
        self._entities = {}
        self._alias_map = {}

        for eid, edata in data.get("entities", {}).items():
            entity = CanonicalEntity.from_dict(edata)
            self._entities[eid] = entity
            # Rebuild alias map
            norm_canonical = normalize_entity_text(entity.canonical_name)
            if norm_canonical:
                self._alias_map[norm_canonical] = eid
            for alias in entity.aliases:
                norm_alias = normalize_entity_text(alias)
                if norm_alias:
                    self._alias_map[norm_alias] = eid
