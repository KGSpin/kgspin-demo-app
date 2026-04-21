"""
LocalEntityRegistry — Sprint 25: Cross-document entity resolution via flat files.

Solves the "document-blind" problem: if "Advanced Micro Devices" appears in a
10-K and "AMD" appears in a news clip, the system should treat them as the
same entity with one canonical ID.

Storage architecture:
    entity_registry.jsonl  — One JSON record per entity (metadata + aliases)
    entity_embeddings.npy  — N × 384 float32 matrix (memory-mapped)

Resolution strategy (3-tier):
    1. Exact Match — normalized name + type in alias index (O(1) dict lookup)
    2. Fuzzy Match — embed query, cosine ≥ 0.92 against mmap'd .npy matrix
    3. Type Gate — never merge entities of different base types

VP Mandate (Sprint 25): "No Blind Vector Merging" — type gate checked BEFORE
any embedding comparison. _types_compatible() delegates to TypeRegistry when
available, falls back to _BASE_TYPE_MAP for standalone usage.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .entity_resolution import (
    CanonicalEntity,
    RawEntity,
    ResolvedEntity,
    generate_canonical_id,
    normalize_entity_text,
)

logger = logging.getLogger(__name__)

# Default registry directory: output/registry/ relative to project root.
# VP Mandate (Sprint 30): Data Lake Compliance — keep registry data in the
# workspace so extractions and resolved identities stay together.
DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "output" / "registry"

# Fallback parent-type map for standalone usage (no TypeRegistry).
# VP Refinement: prefer TypeRegistry.is_compatible() when available.
_BASE_TYPE_MAP = {
    "EXECUTIVE": "PERSON",
    "EMPLOYEE": "PERSON",
    "COMPANY": "ORGANIZATION",
    "REGULATOR": "ORGANIZATION",
}

EMBEDDING_DIM = 384


class LocalEntityRegistry:
    """Entity registry backed by JSONL + mmap'd NumPy.

    Storage:
        entity_registry.jsonl  — One JSON record per entity (metadata + aliases)
        entity_embeddings.npy  — N × 384 float32 matrix (memory-mapped)

    Anti-pattern guardrails:
        - No giant in-memory map: _entities holds CanonicalEntity with embedding=None
        - No provenance overwriting: register() always appends source_documents
        - No blind vector merging: type gate before any cosine comparison
        - No save-per-register: _pending_embeddings buffer flushed on save()
    """

    def __init__(
        self,
        registry_dir: Path,
        embedding_engine=None,
        fuzzy_threshold: float = 0.92,
        type_registry=None,
    ):
        self._registry_dir = Path(registry_dir)
        self._registry_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = self._registry_dir / "entity_registry.jsonl"
        self._npy_path = self._registry_dir / "entity_embeddings.npy"

        self._embedding_engine = embedding_engine
        self._fuzzy_threshold = fuzzy_threshold
        self._type_registry = type_registry

        # In-memory indexes (lightweight — no embedding vectors in RAM)
        self._alias_index: Dict[str, List[int]] = {}  # normalized_alias → [row_indices]
        self._id_index: Dict[str, int] = {}  # entity_id → row_index
        self._type_index: Dict[str, List[int]] = {}  # base_type → [row_indices]
        self._entities: List[CanonicalEntity] = []  # row-ordered, embedding=None

        # Embedding matrix: mmap'd from .npy (OS-paged, not in Python heap)
        self._embedding_matrix: Optional[np.ndarray] = None

        # Buffer for new embeddings before save()
        self._pending_embeddings: List[np.ndarray] = []

        # Load existing registry if present
        if self._jsonl_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        text: str,
        entity_type: str,
        context_text: Optional[str] = None,
    ) -> Optional[ResolvedEntity]:
        """Resolve entity mention to canonical entity.

        3-tier strategy:
            1. Exact alias match (O(1) dict lookup)
            2. Fuzzy embedding match (cosine ≥ threshold, type-filtered)
            3. Returns None if no match
        """
        # Tier 1: Exact match
        normalized = normalize_entity_text(text)
        row_indices = self._alias_index.get(normalized)
        if row_indices:
            for row_idx in row_indices:
                entity = self._entities[row_idx]
                # Type gate: reject cross-type matches
                if self._types_compatible(entity_type, entity.entity_type):
                    return ResolvedEntity(
                        entity_id=entity.entity_id,
                        canonical_name=entity.canonical_name,
                        entity_type=entity.entity_type,
                        domain_type=entity.domain_type,
                        match_confidence=1.0,
                        match_method="exact_alias",
                    )

        # Tier 2: Fuzzy match (embedding cosine similarity)
        if self._embedding_engine is not None and self._has_embeddings():
            return self._fuzzy_resolve(text, entity_type)

        return None

    def resolve_batch(
        self, entities: List[RawEntity]
    ) -> List[Optional[ResolvedEntity]]:
        """Batch resolution for efficiency."""
        return [self.resolve(e.text, e.entity_type) for e in entities]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, entity: CanonicalEntity) -> str:
        """Register a canonical entity. Idempotent: merges if ID exists.

        VP Mandate: source_documents always appended, never replaced.
        Aliases are additive. Embeddings buffered for batch save().
        """
        # Generate ID if not set
        if not entity.entity_id:
            entity.entity_id = generate_canonical_id(
                entity.entity_type, entity.canonical_name
            )

        existing_idx = self._id_index.get(entity.entity_id)
        if existing_idx is not None:
            # Merge into existing entity
            existing = self._entities[existing_idx]
            self._merge_entity(existing, entity, existing_idx)
            return existing.entity_id

        # New entity: append
        row_idx = len(self._entities)
        # Store without embedding in RAM (mmap handles that)
        entity_copy = CanonicalEntity(
            entity_id=entity.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            domain_type=entity.domain_type,
            aliases=list(entity.aliases),
            provenance=entity.provenance,
            source_documents=list(entity.source_documents),
            confidence=entity.confidence,
            embedding=None,  # Never in RAM
            metadata=dict(entity.metadata),
        )
        self._entities.append(entity_copy)

        # Build indexes
        self._id_index[entity.entity_id] = row_idx
        self._index_aliases(entity_copy, row_idx)
        self._index_type(entity_copy.entity_type, row_idx)

        # Compute and buffer embedding
        if self._embedding_engine is not None:
            emb = self._embedding_engine.embed(entity.canonical_name)
            self._pending_embeddings.append(emb.astype(np.float32))
        else:
            # Placeholder zero vector (will be recomputed on next load with engine)
            self._pending_embeddings.append(np.zeros(EMBEDDING_DIM, dtype=np.float32))

        logger.debug(
            "[REGISTRY] Registered: '%s' (%s) -> %s",
            entity.canonical_name, entity.entity_type, entity.entity_id,
        )
        return entity.entity_id

    def register_batch(self, entities: List[CanonicalEntity]) -> List[str]:
        """Batch register entities with single embedding call.

        VP Mandate: no save-per-entity. Accumulates all, then caller
        invokes save() once.
        """
        if not entities:
            return []

        # Separate new vs existing
        new_entities: List[CanonicalEntity] = []
        result_ids: List[str] = []

        for entity in entities:
            if not entity.entity_id:
                entity.entity_id = generate_canonical_id(
                    entity.entity_type, entity.canonical_name
                )

            existing_idx = self._id_index.get(entity.entity_id)
            if existing_idx is not None:
                existing = self._entities[existing_idx]
                self._merge_entity(existing, entity, existing_idx)
                result_ids.append(existing.entity_id)
            else:
                new_entities.append(entity)
                result_ids.append(entity.entity_id)

        if not new_entities:
            return result_ids

        # Batch embed all new canonical names at once
        if self._embedding_engine is not None:
            names = [e.canonical_name for e in new_entities]
            embeddings = self._embedding_engine.embed(names).astype(np.float32)
            if embeddings.ndim == 1:
                embeddings = embeddings.reshape(1, -1)
        else:
            embeddings = np.zeros(
                (len(new_entities), EMBEDDING_DIM), dtype=np.float32
            )

        # Append all at once
        for i, entity in enumerate(new_entities):
            row_idx = len(self._entities)
            entity_copy = CanonicalEntity(
                entity_id=entity.entity_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                domain_type=entity.domain_type,
                aliases=list(entity.aliases),
                provenance=entity.provenance,
                source_documents=list(entity.source_documents),
                confidence=entity.confidence,
                embedding=None,
                metadata=dict(entity.metadata),
            )
            self._entities.append(entity_copy)
            self._id_index[entity.entity_id] = row_idx
            self._index_aliases(entity_copy, row_idx)
            self._index_type(entity_copy.entity_type, row_idx)
            self._pending_embeddings.append(embeddings[i])

        logger.debug(
            "[REGISTRY] Batch registered %d new entities (%d merged)",
            len(new_entities), len(entities) - len(new_entities),
        )
        return result_ids

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, entity_id: str) -> Optional[CanonicalEntity]:
        """Look up entity by canonical ID."""
        idx = self._id_index.get(entity_id)
        if idx is not None:
            return self._entities[idx]
        return None

    def search(
        self,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[CanonicalEntity]:
        """Simple substring search."""
        norm_query = normalize_entity_text(query)
        results = []
        for entity in self._entities:
            if entity_type and entity.entity_type != entity_type:
                continue
            norm_name = normalize_entity_text(entity.canonical_name)
            if norm_query in norm_name:
                results.append(entity)
                if len(results) >= limit:
                    break
        return results

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Atomic write: rewrite JSONL + grow .npy with pending embeddings.

        VP Mandate: tempfile + rename + fcntl lock for crash safety.
        """
        self._registry_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write JSONL (atomic: tempfile + rename)
        fd, tmp_jsonl = tempfile.mkstemp(
            dir=str(self._registry_dir), suffix=".jsonl.tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                for entity in self._entities:
                    f.write(json.dumps(entity.to_dict()) + "\n")
                fcntl.flock(f, fcntl.LOCK_UN)
            os.rename(tmp_jsonl, str(self._jsonl_path))
        except Exception:
            if os.path.exists(tmp_jsonl):
                os.unlink(tmp_jsonl)
            raise

        # 2. Write .npy: concatenate existing matrix + pending embeddings
        if self._pending_embeddings:
            existing = self._get_existing_matrix()
            pending = np.array(self._pending_embeddings, dtype=np.float32)
            if existing is not None and existing.shape[0] > 0:
                full_matrix = np.concatenate([existing, pending], axis=0)
            else:
                full_matrix = pending
            np.save(str(self._npy_path), full_matrix)
            self._pending_embeddings = []
            # Re-mmap the new file
            self._embedding_matrix = np.load(
                str(self._npy_path), mmap_mode="r"
            )
        elif self._embedding_matrix is None and len(self._entities) == 0:
            # Empty registry: write empty matrix
            np.save(str(self._npy_path), np.zeros((0, EMBEDDING_DIM), dtype=np.float32))

        # Length Parity Check (VP Mandate)
        self._verify_parity()

        logger.debug(
            "[REGISTRY] Saved: %d entities, matrix shape=%s",
            len(self._entities),
            self._embedding_matrix.shape if self._embedding_matrix is not None else "N/A",
        )

    def _load(self) -> None:
        """Load JSONL + mmap .npy from disk. Validates length parity."""
        # Load JSONL
        self._entities = []
        self._alias_index = {}
        self._id_index = {}
        self._type_index = {}

        with open(self._jsonl_path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entity = CanonicalEntity.from_dict(data)
                entity.embedding = None  # Never in RAM
                row_idx = len(self._entities)
                self._entities.append(entity)
                self._id_index[entity.entity_id] = row_idx
                self._index_aliases(entity, row_idx)
                self._index_type(entity.entity_type, row_idx)
            fcntl.flock(f, fcntl.LOCK_UN)

        # Load .npy (mmap)
        if self._npy_path.exists():
            self._embedding_matrix = np.load(
                str(self._npy_path), mmap_mode="r"
            )

        # Length Parity Check (VP Mandate)
        self._verify_parity()

        logger.debug(
            "[REGISTRY] Loaded: %d entities from %s",
            len(self._entities), self._jsonl_path,
        )

    def _verify_parity(self) -> None:
        """VP Mandate: detect JSONL/.npy row count desync.

        Raises ValueError if the number of JSONL entities doesn't match
        the number of rows in the .npy embedding matrix.
        """
        if self._embedding_matrix is None:
            return
        n_entities = len(self._entities)
        n_pending = len(self._pending_embeddings)
        n_embeddings = self._embedding_matrix.shape[0]
        expected = n_entities - n_pending
        if expected != n_embeddings:
            raise ValueError(
                f"JSONL/.npy parity desync: {n_entities} entities "
                f"({n_pending} pending) but {n_embeddings} embedding rows. "
                f"Expected {expected} committed rows."
            )

    # ------------------------------------------------------------------
    # Type Gate
    # ------------------------------------------------------------------

    def _types_compatible(self, type_a: str, type_b: str) -> bool:
        """Check if two entity types are compatible for merging.

        VP Anti-Pattern Guard: "Blind Vector Merging" — PERSON and
        ORGANIZATION must never merge, even if embeddings are close.

        Delegates to TypeRegistry.is_compatible() when available;
        falls back to _BASE_TYPE_MAP for standalone usage.
        """
        if type_a == type_b:
            return True

        # Prefer TypeRegistry (VP Refinement: don't hardcode in service layer)
        if self._type_registry is not None:
            return (
                self._type_registry.is_compatible(type_a, type_b)
                or self._type_registry.is_compatible(type_b, type_a)
            )

        # Fallback: resolve both to base types
        base_a = _BASE_TYPE_MAP.get(type_a, type_a)
        base_b = _BASE_TYPE_MAP.get(type_b, type_b)
        return base_a == base_b

    # ------------------------------------------------------------------
    # Fuzzy Resolution (Tier 2)
    # ------------------------------------------------------------------

    def _has_embeddings(self) -> bool:
        """Check if there are committed embeddings to search against."""
        if self._embedding_matrix is not None and self._embedding_matrix.shape[0] > 0:
            return True
        return False

    def _fuzzy_resolve(
        self, text: str, entity_type: str
    ) -> Optional[ResolvedEntity]:
        """Fuzzy match via cosine similarity against type-filtered .npy rows.

        O(N) BLAS — acceptable for M1. HNSW for future sprints.
        """
        if self._embedding_matrix is None or self._embedding_engine is None:
            return None

        query_embedding = self._embedding_engine.embed(text)

        # Get base type for filtering
        base_type = self._get_base_type(entity_type)
        compatible_indices = self._get_type_compatible_indices(entity_type)

        if not compatible_indices:
            return None

        # Filter to only committed embeddings (exclude pending)
        n_committed = self._embedding_matrix.shape[0]
        compatible_indices = [i for i in compatible_indices if i < n_committed]
        if not compatible_indices:
            return None

        # Extract type-filtered rows and compute cosine similarity (BLAS)
        idx_array = np.array(compatible_indices)
        filtered_embeddings = self._embedding_matrix[idx_array]
        similarities = np.dot(filtered_embeddings, query_embedding)

        # Find best match above threshold
        best_local_idx = np.argmax(similarities)
        best_score = float(similarities[best_local_idx])

        if best_score >= self._fuzzy_threshold:
            best_row = compatible_indices[best_local_idx]
            entity = self._entities[best_row]
            return ResolvedEntity(
                entity_id=entity.entity_id,
                canonical_name=entity.canonical_name,
                entity_type=entity.entity_type,
                domain_type=entity.domain_type,
                match_confidence=best_score,
                match_method="embedding",
            )

        return None

    # ------------------------------------------------------------------
    # Index Helpers
    # ------------------------------------------------------------------

    def _index_aliases(self, entity: CanonicalEntity, row_idx: int) -> None:
        """Add canonical name + all aliases to the alias index."""
        norm_canonical = normalize_entity_text(entity.canonical_name)
        if norm_canonical:
            if norm_canonical not in self._alias_index:
                self._alias_index[norm_canonical] = []
            if row_idx not in self._alias_index[norm_canonical]:
                self._alias_index[norm_canonical].append(row_idx)
        for alias in entity.aliases:
            norm_alias = normalize_entity_text(alias)
            if norm_alias:
                if norm_alias not in self._alias_index:
                    self._alias_index[norm_alias] = []
                if row_idx not in self._alias_index[norm_alias]:
                    self._alias_index[norm_alias].append(row_idx)

    def _index_type(self, entity_type: str, row_idx: int) -> None:
        """Add row_idx to the type index under the entity's base type."""
        base_type = self._get_base_type(entity_type)
        if base_type not in self._type_index:
            self._type_index[base_type] = []
        self._type_index[base_type].append(row_idx)

    def _get_base_type(self, entity_type: str) -> str:
        """Resolve domain type to base type."""
        if self._type_registry is not None:
            et = self._type_registry.types.get(entity_type)
            if et and et.parent_type:
                return et.parent_type
        return _BASE_TYPE_MAP.get(entity_type, entity_type)

    def _get_type_compatible_indices(self, entity_type: str) -> List[int]:
        """Get all row indices for entities compatible with entity_type."""
        base_type = self._get_base_type(entity_type)
        return list(self._type_index.get(base_type, []))

    def _merge_entity(
        self, existing: CanonicalEntity, incoming: CanonicalEntity, row_idx: int
    ) -> None:
        """Merge incoming entity into existing. Additive only."""
        # Merge aliases
        for alias in incoming.aliases:
            norm_alias = normalize_entity_text(alias)
            if norm_alias:
                if norm_alias not in self._alias_index:
                    self._alias_index[norm_alias] = []
                if row_idx not in self._alias_index[norm_alias]:
                    existing.aliases.append(alias)
                    self._alias_index[norm_alias].append(row_idx)
        # Also add incoming canonical name as alias if different
        if incoming.canonical_name != existing.canonical_name:
            norm_inc = normalize_entity_text(incoming.canonical_name)
            if norm_inc:
                if norm_inc not in self._alias_index:
                    self._alias_index[norm_inc] = []
                if row_idx not in self._alias_index[norm_inc]:
                    existing.aliases.append(incoming.canonical_name)
                    self._alias_index[norm_inc].append(row_idx)
        # Merge source_documents (VP Mandate: never overwrite provenance)
        for doc in incoming.source_documents:
            if doc not in existing.source_documents:
                existing.source_documents.append(doc)

        logger.debug(
            "[REGISTRY] Merged into '%s': +%d aliases, +%d docs",
            existing.canonical_name,
            len(incoming.aliases),
            len(incoming.source_documents),
        )

    def _get_existing_matrix(self) -> Optional[np.ndarray]:
        """Get the committed embedding matrix (excluding pending)."""
        if self._embedding_matrix is not None:
            return np.array(self._embedding_matrix)  # Copy from mmap
        return None

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def to_summary(self) -> Dict[str, Any]:
        """Summary stats for CLI output."""
        type_counts: Dict[str, int] = {}
        for entity in self._entities:
            base = self._get_base_type(entity.entity_type)
            type_counts[base] = type_counts.get(base, 0) + 1

        total_aliases = sum(len(indices) for indices in self._alias_index.values())
        return {
            "total_entities": len(self._entities),
            "total_aliases": total_aliases,
            "type_distribution": type_counts,
            "registry_dir": str(self._registry_dir),
            "has_embeddings": self._has_embeddings(),
        }
