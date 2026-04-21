"""
Entity resolution and identity services.

Provides pluggable entity resolution so the extraction pipeline can resolve
discovered entity mentions to canonical entities in a registry.
"""

from .clinical_query import derive_clinical_query_from_nct
from .entity_resolution import (
    CanonicalEntity,
    EntityResolutionService,
    JSONFileEntityService,
    RawEntity,
    ResolvedEntity,
    build_normalization_tokens,
    configure_normalization_tokens,
    generate_canonical_id,
    normalize_entity_text,
)
from .registry import LocalEntityRegistry

__all__ = [
    "CanonicalEntity",
    "EntityResolutionService",
    "JSONFileEntityService",
    "LocalEntityRegistry",
    "RawEntity",
    "ResolvedEntity",
    "build_normalization_tokens",
    "configure_normalization_tokens",
    "derive_clinical_query_from_nct",
    "generate_canonical_id",
    "normalize_entity_text",
]
