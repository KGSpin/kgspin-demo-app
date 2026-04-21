# ADR-XXX: {Decision Title}

**Status:** Proposed | Accepted | Deprecated | Superseded by ADR-YYY
**Author:** VP of Engineering
**Created:** {YYYY-MM-DD}
**Last Updated:** {YYYY-MM-DD}
**Deciders:** {CEO, VP Eng, VP Prod — who approved}
**PRD:** {PRD-XXX or "Architecture-driven"}

---

## RICE Score (for prioritization against competing decisions)

| Factor | Value | Rationale |
|--------|-------|-----------|
| **Reach** | {1-10: how many components/workflows does this affect?} | {scope of impact} |
| **Impact** | {1-5: severity if wrong? 1=cosmetic, 3=significant rework, 5=system failure} | {consequences of not deciding} |
| **Confidence** | {0.5-1.0: how proven is the proposed approach?} | {evidence: benchmarks, prototypes, prior art} |
| **Effort** | {T-shirt: XS=0.5, S=1, M=2, L=3, XL=5 sprints} | {implementation cost} |
| **RICE Score** | **{R x I x C / E}** | |

---

## Context

{What is the problem or tension? What forces are at play? Why does this decision need to be made now?}

## Decision

{The choice made. Be specific — not "we will use a cache" but "we will use Redis with a 5-minute TTL on entity lookups."}

## Alternatives Considered

### Alternative 1: {Name}
- **Pros:** {advantages}
- **Cons:** {disadvantages}
- **Why rejected:** {specific reason}

### Alternative 2: {Name}
- **Pros:** {advantages}
- **Cons:** {disadvantages}
- **Why rejected:** {specific reason}

## Consequences

### Positive
- {Benefit 1}
- {Benefit 2}

### Negative
- {Trade-off 1}
- {Trade-off 2}

### Risks
- {What could go wrong and mitigation strategy}

## Implementation Notes

{High-level guidance for the dev team — approach, not code. Reference patterns, not implementations.}

## Related Decisions

| ADR | Relationship |
|-----|-------------|
| {ADR-XXX} | {depends on / supersedes / extends} |

## References

| Document | Link |
|----------|------|
| {PRD, research, external resource} | {path or URL} |
