# PRD-043: Topological Seed-Anchor & Connectivity Sieve

> [!IMPORTANT]
> **RELOCATE**: This PRD belongs to the engine-physics domain and should be relocated to `kgspin-core` upon completion of the modular transition.

**Status:** Approved (Backlog)
**Milestone:** Phase 2 (Quality)
**RICE Score:** 3.6 (Reach: 3, Impact: 3, Confidence: 0.8, Effort: 2)
**Effort:** M (Graph-algorithm based)
**Dependencies:** Relationship extraction must be complete; requires at least one SEED entity
**Last Updated:** 2026-04-19

> **Sprint 11 note (2026-04-19):** RICE Confidence raised from prior
> value to 0.8 per VP Prod 2026-04-17 consultation. Justification:
> Sprint 11's cross-domain news landers (`marketaux`, `yahoo_rss`,
> `newsapi`) provide the "news inputs" that were previously
> theoretical for the clinical-domain seed graph — inputs now exist
> in the CORPUS_DOCUMENT registry and can be consumed when this
> sieve is implemented.

**Origin:** IDEO Sprint — Precision Refinement (Rank 4, 1 vote)

---

## Goal

Prune "Island Nodes" — entities with no topological connection to high-confidence Seed entities (Ticker, CEO, key regulators) — from the final knowledge graph. This is a post-extraction precision sieve that leverages graph structure rather than text signals.

## Background

The Sprint 118 overnight experiment revealed that KGSpin extracts hundreds of entities that have no meaningful connection to the document's primary subjects. Entities like "Address", "Table", "Note" appear as disconnected nodes. By treating the KG as a network and requiring connectivity to known Seed entities, we can prune orphans without risking recall on connected entities.

## Requirements

### Must Have

1. **Seed Entity Definition:** Bundle YAML defines `seed_entities` — a list of entity texts or patterns that act as graph anchors (e.g., ticker symbol, company name, CEO name). These come from `domain_seed_file` or are injected by the extraction pipeline.
2. **Hop-Limit Pruning:** After relationship extraction is complete, compute shortest path from every entity node to the nearest Seed. Entities with no path within `max_seed_hops` (default: 3) are demoted to a "pruned_islands" DLQ bucket.
3. **Blast Radius Protection:** Hyper-connected "hub" nodes (degree > `max_hub_degree`, default: 20) that act as generic sinks are automatically pruned. This catches terms like "United States" that connect to everything but add no specificity.
4. **Opt-In Activation:** The sieve is only active when `seed_entities` is non-empty in the bundle. Bundles without seeds skip this sieve entirely — no forced requirement on all corpora.

### Nice to Have

1. **Anchor Tiers:** Tier 1 (direct seed connection, hop=1) entities get a confidence boost. Tier 2 (hop=2-3) entities pass unchanged. Beyond Tier 2, entities are pruned.
2. **Hub Whitelist:** Allow specific high-degree nodes to be exempt from blast radius pruning (e.g., the ticker company itself will naturally have high degree).

## Success Metrics

- 100% of visible graph nodes are connected to at least one Seed within `max_seed_hops`
- Reduction in "Island" false positives by >60%
- Zero recall loss for entities that are topologically connected to Seeds

## Design Constraints

- Must run AFTER all relationship extraction is complete (post-CleaningSieve)
- Must be deterministic (zero tokens)
- Must be configurable per-domain via bundle YAML
- Must produce DLQ entries for pruned entities (auditable)
- For 10-K filings, the ticker entity is a natural seed. For other corpus types (academic papers, news), the "primary subject" concept may not apply — hence opt-in only.

## Open Questions

1. How do we handle corpora without a natural "root entity"? (Answer: opt-in — sieve is skipped if no seeds defined)
2. Should we consider weighted edges (confidence-weighted shortest path) or unweighted? Start with unweighted for simplicity.
3. What about entities that are only connected via pruned hub nodes? They become islands after hub pruning. Run island pruning AFTER hub pruning.

## Implementation Sketch

```python
class ConnectivitySieve:
    def __init__(self, seed_entities: List[str], max_hops: int = 3, max_hub_degree: int = 20):
        ...

    def run(self, entities: List[Entity], relationships: List[Relationship]) -> SieveResult:
        # 1. Build adjacency graph from relationships
        # 2. Identify seed nodes (exact/fuzzy match against seed_entities)
        # 3. BFS from all seeds — mark reachable nodes within max_hops
        # 4. Prune hub nodes exceeding max_hub_degree (except seeds)
        # 5. Re-run BFS after hub pruning
        # 6. Demote unreachable nodes to DLQ
        ...
```

## Bundle YAML Integration

```yaml
# In domain config
connectivity_sieve:
  enabled: true
  max_seed_hops: 3
  max_hub_degree: 20
  hub_whitelist: []  # entities exempt from hub pruning
```

Seeds come from the existing `domain_seed_file` mechanism — no new config needed for seed definition.

---

*This PRD is approved for the backlog. Implementation will be scheduled after the Type-Exclusivity sieve (PRD-043) is complete and validated.*
