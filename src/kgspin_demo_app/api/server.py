"""
FastAPI Server for kgspin-demo SaaS API.

Provides REST API endpoints for knowledge graph extraction,
designed for programmatic access and SaaS deployment.

Usage:
    # Run with uvicorn
    uvicorn kgspin_demo_app.api.server:app --reload

    # Or via uv
    uv run uvicorn kgspin_demo_app.api.server:app --host 0.0.0.0 --port 8000
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache

try:
    from fastapi import FastAPI, HTTPException, Depends, Header, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None
    # Provide stubs when FastAPI/Pydantic not available
    class BaseModel:
        pass
    def Field(*args, **kwargs):
        return None

from kgspin_core.execution.extractor import ExtractionBundle, KnowledgeGraphExtractor
from kgspin_core.execution.embeddings import get_embedding_engine
from kgspin_core.cli.utils import load_bundle, save_bundle, load_patterns_from_file, patterns_to_definitions
from kgspin_core.agents.pattern_compiler import PatternCompilerAgent
from kgspin_core.tools.linker_tool import LinkerTool

try:
    from kgspin_interface.version import INSTALLATION_CONFIG_SCHEMA_V1
except ImportError:
    INSTALLATION_CONFIG_SCHEMA_V1 = 1


# Request/Response Models
class EntityRequest(BaseModel):
    """Request for entity extraction."""
    text: str = Field(..., description="Text to extract entities from")
    labels: List[str] = Field(
        default=["PERSON", "ORGANIZATION", "LOCATION", "DATE", "MONEY"],
        description="Entity labels to extract"
    )
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence threshold")


class ExtractionMetadata(BaseModel):
    """Phase 2 INSTALLATION — triple-hash provenance attached to every
    extraction-returning response.

    Field order is pinned for stable JSON serialization (Pydantic v2
    preserves declaration order). Any of the hash fields may be ``None``
    on legacy paths or when admin is unreachable; the customer-facing
    doc explains the semantics.
    """
    schema_version: int = Field(
        default=INSTALLATION_CONFIG_SCHEMA_V1,
        description="kgspin-interface InstallationConfig schema version",
    )
    pipeline_version_hash: Optional[str] = Field(
        None, description="pipeline version (kgspin-core git commit + interface schema)"
    )
    bundle_version_hash: Optional[str] = Field(
        None, description="domain bundle version (canonical bundle YAML hash)"
    )
    installation_version_hash: Optional[str] = Field(
        None, description="deployment configuration version (admin InstallationConfig hash)"
    )


def _build_extraction_metadata(provenance: Any) -> ExtractionMetadata:
    """Lift the triple-hash off ``result.provenance`` into the wire shape.

    Empty strings (the kgspin-core migration-window default) are
    surfaced as ``None`` so the customer-facing surface has one
    "unset" representation instead of two.
    """
    def _norm(value: Optional[str]) -> Optional[str]:
        return value if value else None

    return ExtractionMetadata(
        schema_version=INSTALLATION_CONFIG_SCHEMA_V1,
        pipeline_version_hash=_norm(getattr(provenance, "pipeline_version_hash", None)),
        bundle_version_hash=_norm(getattr(provenance, "bundle_version_hash", None)),
        installation_version_hash=_norm(getattr(provenance, "installation_version_hash", None)),
    )


class EntityResponse(BaseModel):
    """Response from entity extraction."""
    entities: List[Dict[str, Any]]
    count: int
    processing_time_ms: float
    extraction_metadata: Optional[ExtractionMetadata] = Field(
        None,
        description="Triple-hash provenance (Phase 2). None for entity-only "
                    "GLiNER calls that don't touch the orchestrator.",
    )


class RelationshipRequest(BaseModel):
    """Request for relationship extraction."""
    text: str = Field(..., description="Text containing entities and relationships")
    bundle_name: Optional[str] = Field(None, description="Bundle name to use (e.g., 'financial')")
    source_document: str = Field(default="api-input", description="Source document identifier")


class RelationshipResponse(BaseModel):
    """Response from relationship extraction."""
    entities: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    # Deprecated: kept flat for one release window so existing callers
    # don't break when extraction_metadata.bundle_version_hash arrives.
    bundle_version: str
    processing_time_ms: float
    extraction_metadata: ExtractionMetadata


class ReplayRequest(BaseModel):
    """Request to replay an extraction with a pinned triple-hash.

    The platform verifies all three hashes match the currently-loaded
    deployment. On any mismatch, the endpoint returns 409 with the
    installed values so the caller can see what version this deployment
    is on.
    """
    text: str = Field(..., description="Document text to re-extract from")
    source_document: str = Field(default="api-replay")
    bundle_name: Optional[str] = Field(None)
    pipeline_version_hash: str = Field(..., description="Required pin")
    bundle_version_hash: str = Field(..., description="Required pin")
    installation_version_hash: str = Field(..., description="Required pin")


class EstablishRelationshipRequest(BaseModel):
    """Request to check for a specific relationship."""
    entity_a: Dict[str, str] = Field(..., description="Subject entity with 'text' and 'entity_type'")
    entity_b: Dict[str, str] = Field(..., description="Object entity with 'text' and 'entity_type'")
    context: str = Field(..., description="Sentence or paragraph containing both entities")
    bundle_name: Optional[str] = Field(None, description="Bundle name to use")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class CompileRequest(BaseModel):
    """Request to compile patterns into a bundle."""
    patterns: Dict[str, Any] = Field(..., description="Pattern definitions in YAML/JSON format")
    bundle_name: str = Field(..., description="Name for the output bundle")
    version: str = Field(default="v1.0.0", description="Version string")


class CompileResponse(BaseModel):
    """Response from bundle compilation."""
    success: bool
    bundle_name: str
    version: str
    entity_fingerprints: List[str]
    relationship_fingerprints: List[str]
    relationship_constraints: Dict[str, Dict[str, List[str]]]


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    timestamp: str


# Bundle cache
_bundle_cache: Dict[str, ExtractionBundle] = {}
_linker_cache: Dict[str, LinkerTool] = {}


def get_bundle(bundle_name: Optional[str] = None) -> ExtractionBundle:
    """Get a bundle by name, using cache."""
    if bundle_name is None:
        bundle_name = "demo"

    if bundle_name not in _bundle_cache:
        if bundle_name == "demo":
            from ..execution.extractor import create_demo_bundle
            _bundle_cache[bundle_name] = create_demo_bundle()
        else:
            # Look for bundle in configured bundles directory
            bundles_dir = Path(os.environ.get("KGEN_BUNDLES_DIR", ".bundles"))
            bundle_path = bundles_dir / bundle_name
            if bundle_path.exists():
                bundle, _, _ = load_bundle(bundle_path)
                _bundle_cache[bundle_name] = bundle
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Bundle '{bundle_name}' not found"
                )

    return _bundle_cache[bundle_name]


def get_linker(bundle_name: Optional[str] = None) -> LinkerTool:
    """Get a linker for a specific bundle."""
    cache_key = bundle_name or "demo"
    if cache_key not in _linker_cache:
        bundle = get_bundle(bundle_name)
        _linker_cache[cache_key] = LinkerTool(bundle=bundle)
    return _linker_cache[cache_key]


def create_app() -> "FastAPI":
    """Create and configure the FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "FastAPI not installed. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="KGenSkills Extraction API",
        description="Knowledge Graph Extraction API - Extract entities and relationships from text",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key validation (optional)
    async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> Optional[str]:
        """Verify API key if authentication is enabled."""
        required_key = os.environ.get("KGEN_API_KEY")
        if required_key and x_api_key != required_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key"
            )
        return x_api_key

    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Health check endpoint."""
        return HealthResponse(
            status="healthy",
            version="1.0.0",
            timestamp=datetime.now(timezone.utc).isoformat()
        )

    @app.get("/bundles")
    async def list_bundles(api_key: Optional[str] = Depends(verify_api_key)):
        """List available extraction bundles."""
        bundles_dir = Path(os.environ.get("KGEN_BUNDLES_DIR", ".bundles"))
        bundles = ["demo"]  # Always available

        if bundles_dir.exists():
            for item in bundles_dir.iterdir():
                if item.is_dir() and (item / "bundle.json").exists():
                    bundles.append(item.name)

        return {"bundles": bundles}

    @app.get("/bundles/{bundle_name}")
    async def get_bundle_info(
        bundle_name: str,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """Get information about a specific bundle."""
        bundle = get_bundle(bundle_name)
        return {
            "name": bundle_name,
            "version": bundle.version,
            "entity_fingerprints": list(bundle.entity_fingerprints.keys()),
            "relationship_fingerprints": list(bundle.relationship_fingerprints.keys()),
            "relationship_constraints": bundle.relationship_constraints,
            "confidence_threshold": bundle.confidence_threshold,
            "q_head_threshold": bundle.q_head_threshold
        }

    @app.post("/extract/entities", response_model=EntityResponse)
    async def extract_entities(
        request: EntityRequest,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """Extract named entities from text using GLiNER."""
        start_time = time.time()

        try:
            from gliner import GLiNER
            model = GLiNER.from_pretrained("urchade/gliner_base")
            entities = model.predict_entities(
                request.text,
                request.labels,
                threshold=request.threshold
            )

            processing_time = (time.time() - start_time) * 1000

            return EntityResponse(
                entities=[
                    {
                        "text": e["text"],
                        "label": e["label"],
                        "score": e["score"],
                        "start": e.get("start"),
                        "end": e.get("end")
                    }
                    for e in entities
                ],
                count=len(entities),
                processing_time_ms=processing_time,
                extraction_metadata=None,
            )

        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="GLiNER not installed. Server configuration error."
            )

    @app.post("/extract/relationships", response_model=RelationshipResponse)
    async def extract_relationships(
        request: RelationshipRequest,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """Extract relationships from text using semantic fingerprints."""
        start_time = time.time()

        bundle = get_bundle(request.bundle_name)
        extractor = KnowledgeGraphExtractor(bundle)
        result = extractor.extract(request.text, request.source_document)

        processing_time = (time.time() - start_time) * 1000

        return RelationshipResponse(
            entities=[
                {
                    "text": e.text,
                    "type": e.entity_type,
                    "confidence": e.confidence
                }
                for e in result.entities
            ],
            relationships=[
                {
                    "subject": r.subject.text,
                    "predicate": r.predicate,
                    "object": r.object.text,
                    "confidence": r.confidence,
                    "evidence": r.evidence.sentence_text if r.evidence else None
                }
                for r in result.relationships
            ],
            bundle_version=bundle.version,
            processing_time_ms=processing_time,
            extraction_metadata=_build_extraction_metadata(result.provenance),
        )

    @app.post("/extract/establish")
    async def establish_relationship(
        request: EstablishRelationshipRequest,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """Check if a specific relationship exists between two entities."""
        linker = get_linker(request.bundle_name)
        bundle = get_bundle(request.bundle_name)

        result = linker.establish_relationship(
            entity_a=request.entity_a,
            entity_b=request.entity_b,
            context=request.context,
            threshold=request.threshold
        )

        # Phase 2 INSTALLATION (CTO 2026-04-26) — establish does not run
        # the orchestrator and therefore has no Provenance to lift from.
        # Surface the bundle hash we *can* compute; pipeline/installation
        # are resolved on the live extractor, not the linker, so they
        # surface as None on this endpoint.
        try:
            from kgspin_core.provenance import bundle_version_hash as _bvh
            bundle_payload = bundle.model_dump(mode="json") if hasattr(bundle, "model_dump") else {}
            bundle_hash = _bvh(bundle_payload) if bundle_payload else None
        except Exception:
            bundle_hash = None

        metadata = ExtractionMetadata(
            schema_version=INSTALLATION_CONFIG_SCHEMA_V1,
            pipeline_version_hash=None,
            bundle_version_hash=bundle_hash,
            installation_version_hash=None,
        ).model_dump()

        if result:
            return {
                "found": True,
                "relationship": {
                    "subject": result["subject"]["text"],
                    "predicate": result["predicate"],
                    "object": result["object"]["text"],
                    "confidence": result["confidence"]
                },
                "extraction_metadata": metadata,
            }
        else:
            return {
                "found": False,
                "message": "No relationship detected above threshold",
                "extraction_metadata": metadata,
            }

    @app.post("/extract/replay/relationships")
    async def replay_relationships(
        request: ReplayRequest,
        api_key: Optional[str] = Depends(verify_api_key),
    ):
        """Replay an extraction with a pinned triple-hash.

        Phase 2 INSTALLATION (CTO 2026-04-26). This is the *match-or-409*
        replay surface: the request's triple must match the deployment's
        currently-loaded `(pipeline, bundle, installation)` triple. On
        mismatch, returns 409 with both `requested` and `installed`
        triples so the caller can see what version this deployment is on.

        Per-historical-hash replay (fetch arbitrary bundle/installation
        by hash, build a fresh extractor, run, return) is a documented
        Phase 2.1 follow-up — see `docs/reproducibility-by-triple-hash.md`.
        """
        bundle = get_bundle(request.bundle_name)
        extractor = KnowledgeGraphExtractor(bundle)
        start_time = time.time()
        result = extractor.extract(request.text, request.source_document)
        processing_time = (time.time() - start_time) * 1000

        installed_meta = _build_extraction_metadata(result.provenance)

        requested = (
            request.pipeline_version_hash,
            request.bundle_version_hash,
            request.installation_version_hash,
        )
        installed = (
            installed_meta.pipeline_version_hash or "",
            installed_meta.bundle_version_hash or "",
            installed_meta.installation_version_hash or "",
        )
        if requested != installed:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "triple_hash_mismatch",
                    "message": (
                        "The requested triple-hash does not match this "
                        "deployment's currently-loaded triple. See "
                        "docs/reproducibility-by-triple-hash.md for the "
                        "pinning workflow."
                    ),
                    "requested": {
                        "pipeline_version_hash": requested[0],
                        "bundle_version_hash": requested[1],
                        "installation_version_hash": requested[2],
                    },
                    "installed": installed_meta.model_dump(),
                },
            )

        return RelationshipResponse(
            entities=[
                {
                    "text": e.text,
                    "type": e.entity_type,
                    "confidence": e.confidence,
                }
                for e in result.entities
            ],
            relationships=[
                {
                    "subject": r.subject.text,
                    "predicate": r.predicate,
                    "object": r.object.text,
                    "confidence": r.confidence,
                    "evidence": r.evidence.sentence_text if r.evidence else None,
                }
                for r in result.relationships
            ],
            bundle_version=bundle.version,
            processing_time_ms=processing_time,
            extraction_metadata=installed_meta,
        )

    @app.post("/compile", response_model=CompileResponse)
    async def compile_bundle(
        request: CompileRequest,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """Compile pattern definitions into an extraction bundle."""
        try:
            definitions, registry = patterns_to_definitions(request.patterns)

            compiler = PatternCompilerAgent()
            bundle, results = compiler.compile_bundle_sync(definitions, request.version)

            # Save to bundles directory
            bundles_dir = Path(os.environ.get("KGEN_BUNDLES_DIR", ".bundles"))
            output_path = bundles_dir / request.bundle_name
            save_bundle(bundle, registry, output_path, metadata={
                "domain": request.patterns.get("domain", "custom")
            })

            # Update cache
            _bundle_cache[request.bundle_name] = bundle

            return CompileResponse(
                success=True,
                bundle_name=request.bundle_name,
                version=request.version,
                entity_fingerprints=list(bundle.entity_fingerprints.keys()),
                relationship_fingerprints=list(bundle.relationship_fingerprints.keys()),
                relationship_constraints=bundle.relationship_constraints
            )

        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Compilation failed: {str(e)}"
            )

    @app.get("/relationships/{bundle_name}")
    async def list_relationship_types(
        bundle_name: str,
        api_key: Optional[str] = Depends(verify_api_key)
    ):
        """List available relationship types in a bundle."""
        linker = get_linker(bundle_name)
        rel_types = linker.get_available_relationships()

        return {
            "bundle": bundle_name,
            "relationship_types": [
                linker.get_relationship_info(rel_type)
                for rel_type in rel_types
            ]
        }

    return app


# Create the default app instance
if FASTAPI_AVAILABLE:
    app = create_app()
else:
    app = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
