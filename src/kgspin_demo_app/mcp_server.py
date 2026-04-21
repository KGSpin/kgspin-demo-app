"""
MCP Server for kgspin-demo - Claude Desktop Integration.

This module exposes knowledge graph extraction capabilities as MCP tools
for use with Claude Desktop and other MCP-compatible clients.

Tools exposed:
- extract_entities: Extract entities from text using GLiNER
- extract_relationships: Extract relationships using semantic fingerprints
- compile_bundle: Compile patterns into extraction bundle

Usage:
    # Run as MCP server
    python -m kgspin_demo_app.mcp_server

    # Or via uv
    uv run python -m kgspin_demo_app.mcp_server
"""

import json
import asyncio
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    Server = None

from .execution.extractor import ExtractionBundle, KnowledgeGraphExtractor
from .execution.embeddings import get_embedding_engine
from .cli.utils import load_bundle, save_bundle, load_patterns_from_file, patterns_to_definitions
from .agents.pattern_compiler import PatternCompilerAgent
from .tools.linker_tool import LinkerTool


def create_mcp_server() -> "Server":
    """Create and configure the MCP server."""
    if not MCP_AVAILABLE:
        raise ImportError(
            "MCP package not installed. Install with: pip install mcp"
        )

    server = Server("kgen-extract")

    # Cached bundle and tools
    _bundle_cache: dict[str, ExtractionBundle] = {}
    _linker: Optional[LinkerTool] = None

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List available MCP tools."""
        return [
            Tool(
                name="extract_entities",
                description="Extract named entities from text using GLiNER NER model",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to extract entities from"
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Entity labels to extract (e.g., ['PERSON', 'ORGANIZATION'])",
                            "default": ["PERSON", "ORGANIZATION", "LOCATION", "DATE", "MONEY"]
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Minimum confidence threshold (0.0-1.0)",
                            "default": 0.5
                        }
                    },
                    "required": ["text"]
                }
            ),
            Tool(
                name="extract_relationships",
                description="Extract relationships between entities using semantic fingerprints",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text containing entities and relationships"
                        },
                        "bundle_path": {
                            "type": "string",
                            "description": "Path to extraction bundle directory (optional, uses demo bundle if not specified)"
                        },
                        "source_document": {
                            "type": "string",
                            "description": "Source document identifier",
                            "default": "mcp-input"
                        }
                    },
                    "required": ["text"]
                }
            ),
            Tool(
                name="compile_bundle",
                description="Compile pattern definitions into an extraction bundle",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "patterns_path": {
                            "type": "string",
                            "description": "Path to YAML/JSON pattern file"
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Path to output bundle directory"
                        },
                        "version": {
                            "type": "string",
                            "description": "Version string for the bundle",
                            "default": "v1.0.0"
                        }
                    },
                    "required": ["patterns_path", "output_path"]
                }
            ),
            Tool(
                name="establish_relationship",
                description="Check if a specific relationship exists between two entities",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_a": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "entity_type": {"type": "string"}
                            },
                            "required": ["text", "entity_type"],
                            "description": "Subject entity"
                        },
                        "entity_b": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "entity_type": {"type": "string"}
                            },
                            "required": ["text", "entity_type"],
                            "description": "Object entity"
                        },
                        "context": {
                            "type": "string",
                            "description": "Sentence or paragraph containing both entities"
                        },
                        "bundle_path": {
                            "type": "string",
                            "description": "Path to extraction bundle (optional)"
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Minimum confidence threshold",
                            "default": 0.5
                        }
                    },
                    "required": ["entity_a", "entity_b", "context"]
                }
            ),
            Tool(
                name="list_relationship_types",
                description="List available relationship types in a bundle",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "bundle_path": {
                            "type": "string",
                            "description": "Path to extraction bundle (optional, uses demo bundle if not specified)"
                        }
                    }
                }
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle tool calls."""
        nonlocal _linker

        if name == "extract_entities":
            return await _extract_entities(arguments)
        elif name == "extract_relationships":
            return await _extract_relationships(arguments, _bundle_cache)
        elif name == "compile_bundle":
            return await _compile_bundle(arguments)
        elif name == "establish_relationship":
            if _linker is None:
                _linker = LinkerTool()
            return await _establish_relationship(arguments, _linker, _bundle_cache)
        elif name == "list_relationship_types":
            if _linker is None:
                _linker = LinkerTool()
            return await _list_relationship_types(arguments, _linker, _bundle_cache)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _extract_entities(arguments: dict[str, Any]) -> list:
    """Extract entities using GLiNER."""
    text = arguments["text"]
    labels = arguments.get("labels", ["PERSON", "ORGANIZATION", "LOCATION", "DATE", "MONEY"])
    threshold = arguments.get("threshold", 0.5)

    try:
        from gliner import GLiNER
        model = GLiNER.from_pretrained("urchade/gliner_base")
        entities = model.predict_entities(text, labels, threshold=threshold)

        result = {
            "entities": [
                {
                    "text": e["text"],
                    "label": e["label"],
                    "score": e["score"],
                    "start": e.get("start"),
                    "end": e.get("end")
                }
                for e in entities
            ],
            "count": len(entities)
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except ImportError:
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": "GLiNER not installed. Install with: pip install gliner"
            })
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


async def _extract_relationships(
    arguments: dict[str, Any],
    bundle_cache: dict[str, ExtractionBundle]
) -> list:
    """Extract relationships using semantic fingerprints."""
    text = arguments["text"]
    bundle_path = arguments.get("bundle_path")
    source_document = arguments.get("source_document", "mcp-input")

    try:
        # Load or get cached bundle
        if bundle_path:
            if bundle_path not in bundle_cache:
                bundle, _, _ = load_bundle(Path(bundle_path))
                bundle_cache[bundle_path] = bundle
            bundle = bundle_cache[bundle_path]
        else:
            from .execution.extractor import create_demo_bundle
            bundle = create_demo_bundle()

        # Create extractor and run
        extractor = KnowledgeGraphExtractor(bundle)
        result = extractor.extract(text, source_document)

        output = {
            "entities": [
                {
                    "text": e.text,
                    "type": e.entity_type,
                    "confidence": e.confidence
                }
                for e in result.entities
            ],
            "relationships": [
                {
                    "subject": r.subject.text,
                    "predicate": r.predicate,
                    "object": r.object.text,
                    "confidence": r.confidence,
                    "evidence": r.evidence.sentence_text if r.evidence else None
                }
                for r in result.relationships
            ],
            "bundle_version": bundle.version,
            "provenance": {
                "total_entities": result.provenance.total_entities,
                "total_relationships": result.provenance.total_relationships
            }
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


async def _compile_bundle(arguments: dict[str, Any]) -> list:
    """Compile patterns into extraction bundle."""
    patterns_path = Path(arguments["patterns_path"])
    output_path = Path(arguments["output_path"])
    version = arguments.get("version", "v1.0.0")

    try:
        # Load patterns
        patterns = load_patterns_from_file(patterns_path)
        definitions, registry = patterns_to_definitions(patterns)

        # Compile bundle
        compiler = PatternCompilerAgent()
        bundle, results = compiler.compile_bundle_sync(definitions, version)

        # Save bundle
        save_bundle(bundle, registry, output_path, metadata={
            "source_patterns": str(patterns_path),
            "domain": patterns.get("domain", "unknown")
        })

        # Summary
        successful = sum(1 for r in results if r.success)
        output = {
            "success": True,
            "bundle_path": str(output_path),
            "version": version,
            "patterns_compiled": successful,
            "patterns_failed": len(results) - successful,
            "entity_fingerprints": list(bundle.entity_fingerprints.keys()),
            "relationship_fingerprints": list(bundle.relationship_fingerprints.keys()),
            "relationship_constraints": bundle.relationship_constraints
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e), "success": False})
        )]


async def _establish_relationship(
    arguments: dict[str, Any],
    linker: LinkerTool,
    bundle_cache: dict[str, ExtractionBundle]
) -> list:
    """Check if a relationship exists between two entities."""
    entity_a = arguments["entity_a"]
    entity_b = arguments["entity_b"]
    context = arguments["context"]
    bundle_path = arguments.get("bundle_path")
    threshold = arguments.get("threshold", 0.5)

    try:
        # Load bundle if specified
        if bundle_path:
            if bundle_path not in bundle_cache:
                bundle, _, _ = load_bundle(Path(bundle_path))
                bundle_cache[bundle_path] = bundle
            linker.set_bundle(bundle_cache[bundle_path])

        # Check for relationship
        result = linker.establish_relationship(
            entity_a, entity_b, context, threshold
        )

        if result:
            output = {
                "found": True,
                "relationship": {
                    "subject": result["subject"]["text"],
                    "predicate": result["predicate"],
                    "object": result["object"]["text"],
                    "confidence": result["confidence"],
                    "fingerprint_version": result.get("fingerprint_version")
                }
            }
        else:
            output = {
                "found": False,
                "message": "No relationship detected above threshold"
            }

        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


async def _list_relationship_types(
    arguments: dict[str, Any],
    linker: LinkerTool,
    bundle_cache: dict[str, ExtractionBundle]
) -> list:
    """List available relationship types."""
    bundle_path = arguments.get("bundle_path")

    try:
        if bundle_path:
            if bundle_path not in bundle_cache:
                bundle, _, _ = load_bundle(Path(bundle_path))
                bundle_cache[bundle_path] = bundle
            linker.set_bundle(bundle_cache[bundle_path])

        rel_types = linker.get_available_relationships()
        output = {
            "relationship_types": rel_types,
            "constraints": {
                rel_type: linker.get_relationship_info(rel_type)
                for rel_type in rel_types
            }
        }
        return [TextContent(type="text", text=json.dumps(output, indent=2))]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e)})
        )]


async def main():
    """Run the MCP server."""
    if not MCP_AVAILABLE:
        print("Error: MCP package not installed. Install with: pip install mcp")
        return

    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
