# PRD-048: Restructure Demo Diagnostics & AI Assessment

**Status:** Deferred
**Milestone:** 1
**RICE Score:** 14.4 (Reach: 4, Impact: 4, Confidence: 0.9, Effort: 1)
**Effort:** S (1-2 days)

**Dependencies:** None
**Last Updated:** 2026-03-06

---

## Goal
To replace the mathematically flawed and inherently biased "H-Score" and "L-Score" diagnostic metrics with a new "KGSpin Performance Delta" framework (Consensus Rate, Relative Yield Factor, Fact Extraction Cost) in both the Demo App UI and the AI Analysis Agent prompt. This ensures the product objectively demonstrates KGSpin's superiority in yield and cost without inaccurately treating the LLM as ground truth.

## Background
Currently, the Demo App calculates H-Score (Entity Recall) and L-Score (Relationship Recall) by using the LLM's output as the denominator (100% Ground Truth). This mathematically punishes KGSpin for high yield—if KGSpin finds 50 valid relationships but misses 1 out of the 2 the LLM found, it scores a mathematically flawed 50%.
Furthermore, the `build_quality_analysis_prompt` used by the AI Analysis Agent is structurally biased. It visually truncates long relationship lists to save tokens, failing to provide the AI with the hard math required to assess yield. Combined with the prompt's inherent LLM sycophancy (favoring "multi-stage LLMs" over "deterministic pipelines"), the AI actively hallucinates wins for the LLM despite overwhelming quantitative evidence to the contrary.

## Requirements

### Must Have
1. **Remove H-Score and L-Score metrics** from the frontend Demo App UI.
2. **Implement "Consensus Rate" metric (UI and Backend):** Calculates the percentage of LLM-extracted facts that were also extracted by KGSpin. (Proves semantic understanding without treating LLM as maximum yield).
3. **Implement "Relative Yield Factor" metric (UI and Backend):** Calculates the ratio of total KGSpin facts to total LLM facts (e.g., "23.5x").
4. **Implement "Fact Extraction Cost" metric (UI and Backend):** Calculates and displays the estimated cost per extracted fact for both systems.
5. **Update AI Analysis Prompt (`build_quality_analysis_prompt`):**
    - Inject the newly calculated Consensus Rate, Relative Yield Factor, and Fact Extraction Cost directly into the prompt text.
    - Add strict rubric instructions: "Do not declare a pipeline the winner if its Relationship yield is significantly lower than the others. Prioritize zero-cost and high-throughput pipelines as winners ONLY IF they achieve equivalent or higher relationship coverage."

### Nice to Have
1. Add a brief tooltip in the UI explaining "Consensus Rate" (e.g., "Overlap with LLM baseline").
2. Standardize metric calculations in a shared utility or dedicated backend endpoint to decouple frontend math from AI prompt data.

## Technical Design
The calculation of Consensus Rate, Relative Yield Factor, and Fact Extraction Cost should be centralized in the backend (potentially as a standalone analysis endpoint or utility function) to ensure the UI and the AI Analysis Agent prompt receive identical data. The frontend should purely render these three new data points, dropping the old score logic. The prompt builder needs string interpolation to inject these hard numbers explicitly before the truncated lists are appended.

## Success Metrics
- 0 instances of AI Analysis Agent selecting LLM as "Winner" when KGSpin relationship yield is > 2x the LLM yield.
- Demo conversion rate increases due to clearer, business-focused metrics (Yield & Cost) rather than debugging jargon (H-Score).

## Dependencies
- Backend extraction logic must return counts of extracted entities and relationships prior to truncation.

## Open Questions
- Should Fact Extraction Cost use a hardcoded LLM token cost, or dynamically calculate based on the specific Anthropic/OpenAI model used in the run?
