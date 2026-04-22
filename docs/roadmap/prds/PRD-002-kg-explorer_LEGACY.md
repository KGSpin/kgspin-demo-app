# PRD-002: KG Explorer Frontend

**Status:** Draft (v2 — two-tier architecture)
**Milestone:** 2
**Effort:** L (2-3 weeks)
**Dependencies:** PRD-001 (Operational DB + Entity Resolution + Graph API)
**Last Updated:** 2026-02-07

---

## Goal

Provide an interactive, enterprise-polished graph visualization at `/explorer` where design partners can see their knowledge graph, click entities to drill into relationships and evidence, filter by corpus and entity type, and see cross-corpus entity linking in action.

---

## Background

The current system produces `.kg.json` files that are only inspectable via text editors or scripts. Design partners need to *see* the knowledge graph — visually explore entities and relationships, click through to source evidence, and understand how data from different sources connects.

The graph explorer is the centerpiece of the demo (Act 1, 10 minutes). It must handle ~500 entities and ~200 relationships smoothly, with click-to-expand for deeper exploration.

ProvenanceView — the ability to click any relationship and see the exact source sentence — is the killer differentiator. Every competitor shows entity-relationship graphs. Nobody else provides full lineage from extracted fact back to source sentence with fingerprint similarity score.

**Architectural context:** The KG Explorer is a **demo/admin tool** that queries the operational SurrealDB (Tier 1). It is NOT the way production customers would explore their knowledge graphs. In production, customers query their own exported data using their preferred tools:
- **PuppyGraph** over Iceberg tables (openCypher graph queries, zero ETL)
- **Apache AGE** on PostgreSQL (openCypher via PostgreSQL extension)
- **DuckDB / Spark SQL / Snowflake** over Parquet or Iceberg files
- Custom dashboards built on the output adapter format (see PRD-006)

The KG Explorer exists to sell the demo, prove the extraction quality, and let us iterate quickly. It does not need to scale to production workloads.

---

## Requirements

### Must Have

1. **Graph rendering** with Cytoscape.js: nodes = entities, edges = relationships
2. **Node styling**: Color by entity_type (PERSON=blue, ORG=green, LOCATION=amber, etc.), size by mention_count
3. **Edge styling**: Color by predicate type, width by confidence, labeled with predicate name
4. **Click node → EntityPanel**: Slide-in panel showing entity details, all relationships, aliases, source documents
5. **Click edge → ProvenanceView**: Shows evidence.sentence_text with both entities highlighted, source document path, extraction method, fingerprint similarity score, bundle version
6. **Double-click node → expand neighborhood**: Fetch and render 1-hop neighbors from API
7. **Filters**: Corpus checkboxes (SEC, healthcare, etc.), entity type toggles, confidence slider
8. **Search**: Search entities by name, center graph on result
9. **Cross-corpus badge**: Visual indicator on entities that appear in multiple corpora
10. **Layout controls**: Toggle between COSE (force-directed), dagre (hierarchical), grid layouts

### Nice to Have

- For derived facts: show full inference chain with premises in ProvenanceView
- Export graph as PNG/SVG
- Shareable graph state via URL parameters
- Entity comparison view (two entities side by side)
- Timeline view for temporal relationships

---

## Technical Design

### Frontend Structure

```
frontend/
  app/
    layout.tsx                  # Sidebar nav, header with global search
    explorer/page.tsx           # Main graph + detail panels
    admin/page.tsx              # Placeholder for M3
    compare/page.tsx            # Placeholder for M4
    onboard/page.tsx            # Placeholder for M5
  components/
    graph/
      KnowledgeGraph.tsx        # Cytoscape.js wrapper (react-cytoscapejs)
      GraphControls.tsx         # Layout toggles, type filters, confidence slider
      GraphLegend.tsx           # Color key for entity types
    entity/
      EntityPanel.tsx           # Right-side slide-in detail panel
      RelationshipList.tsx      # All relationships for selected entity
      ProvenanceView.tsx        # Evidence drilldown — THE differentiator
    layout/
      Sidebar.tsx               # Corpus selector, navigation between pages
      SearchBar.tsx             # Global entity search with typeahead
  lib/
    api.ts                      # Typed FastAPI client (fetch wrappers)
    types.ts                    # TypeScript interfaces matching Python models
    colors.ts                   # Entity type → color mapping
    graph-config.ts             # Cytoscape.js layout/style configuration
```

### Tech Stack

| Library | Version | Purpose |
|---------|---------|---------|
| Next.js | 14+ | App router, server components |
| Tailwind CSS | 3.x | Utility-first styling |
| shadcn/ui | latest | Pre-built component library |
| react-cytoscapejs | 2.x | React wrapper for Cytoscape.js |
| cytoscape | 3.x | Graph rendering + layout algorithms |
| cytoscape-dagre | latest | Hierarchical layout plugin |
| cytoscape-cose-bilkent | latest | Force-directed layout plugin |

### Graph Interaction Model

**Nodes:**
- Shape: rounded rectangle
- Color: mapped to entity_type via consistent palette
- Size: `20px + log(mention_count) * 8px` (prevents giant nodes)
- Label: entity text, truncated to 20 chars
- Badge: small icon if entity appears in 2+ corpora

**Edges:**
- Color: mapped to predicate type (leads=blue, acquired=green, etc.)
- Width: `1px + confidence * 3px`
- Label: predicate name
- Arrow: directed (subject → object)

**Interactions:**
| Action | Result |
|--------|--------|
| Click node | EntityPanel opens on right with details |
| Click edge | ProvenanceView opens showing source evidence |
| Double-click node | Fetch 1-hop neighbors, add to graph |
| Right-click node | Context menu: "Focus", "Hide", "Expand 2-hop" |
| Scroll | Zoom in/out |
| Drag background | Pan |
| Drag node | Reposition (pins in place) |

### ProvenanceView Component (Key Feature)

When clicking any relationship edge, displays:

```
┌─────────────────────────────────────────────┐
│ acquired                              0.91  │
│ AMD → Xilinx                                │
├─────────────────────────────────────────────┤
│ Source Evidence:                             │
│ "We completed our acquisition of            │
│  [Xilinx, Inc.] in February 2022 for        │
│  approximately [AMD] stock..."              │
│                                             │
│ Source: AMD_10K_2023.txt (chunk 4, sent 12) │
│ Method: semantic_fingerprint                │
│ Similarity: 0.913                           │
│ Bundle: financial-v2.0.0                    │
│ Threshold: 0.847                            │
│                                             │
│ [View Full Document] [View in Graph]        │
└─────────────────────────────────────────────┘
```

Entity mentions in the evidence text are highlighted with their type colors. All of this data already exists in every `.kg.json` file's `evidence` fields.

### Data Flow

1. **Page load**: `GET /api/v1/graph/stats` → populate sidebar corpus list + counts
2. **Initial graph**: `GET /api/v1/graph/entities?limit=100` → render top 100 entities by mention_count
3. **Load relationships**: `GET /api/v1/graph/relationships?limit=500` → render edges between visible nodes
4. **Click entity**: `GET /api/v1/graph/entities/{id}` → populate EntityPanel
5. **Expand**: `GET /api/v1/graph/entities/{id}/neighbors?hops=1` → add new nodes/edges to graph
6. **Search**: `GET /api/v1/graph/search?q=lisa+su` → highlight/center matching nodes
7. **Filter**: Client-side filter of already-loaded data, or re-fetch if corpus filter changes

---

## Acceptance Criteria

1. **Renders at scale**: Graph displays 50+ entities from AMD corpus without lag
2. **Node interaction**: Click AMD → EntityPanel shows entity details with leads, acquired, competes_with relationships
3. **Provenance works**: Click "acquired Xilinx" edge → ProvenanceView shows "We completed our acquisition of Xilinx, Inc. in February 2022" with entities highlighted
4. **Cross-corpus**: Toggle healthcare corpus → J&J gets multi-source badge showing it appears in SEC and healthcare data
5. **Search**: Search "Lisa Su" → graph centers on her node, highlighted
6. **Layout toggle**: Switch between force-directed and hierarchical layouts
7. **Confidence filter**: Sliding confidence threshold hides/shows edges dynamically
8. **Performance**: Initial load < 2s, click interactions < 200ms, expand < 500ms

---

## Open Questions

1. **Initial view**: Start with all entities visible, or start with a single entity and expand? For demo, showing everything creates the "wow" factor but may be cluttered.
2. **Node limit**: What's the practical limit for Cytoscape.js before performance degrades? Need to test with real data.
3. **Mobile**: Is mobile/tablet support needed for the demo? Or desktop-only?
4. **Auth**: Does the explorer need authentication for the demo, or is it open access?
