# PRD-039: Analysis Section Redesign (3-Way Matrix)

**Status:** Draft
**Component:** Demo UI ("KGSpin vs LLM" Tab)
**RICE Score:** 14.0 (Reach: 4, Impact: 5, Confidence: 0.7, Effort: 1)
**Effort:** S (1 week)

**Goal:** Redesign the current qualitative "Analysis" section into a structured, quantitative 3-way comparison matrix that definitively computes a "winner."

---

## 1. Context & Problem
Currently, the "Analysis" section on the first tab shows a generic set of pros and cons. It fails to effectively contrast KGSpin against the two specific LLM baselines we run natively in the browser (Gemini Full Shot and Modular Multi-Stage). We need a structured matrix that makes the competitive advantages of KGSpin undeniably clear.

## 2. The 3-Way Matrix Design
The analysis section will be replaced by a grid/table. 

**Columns (The Contenders):**
1.  **KGSpin (Our Engine)**
2.  **LLM Full-Shot (e.g., Gemini Prompting)**
3.  **LLM Multi-Stage (e.g., Agentic Chunking)**

**Rows (The Evaluation Criteria):**
We need to finalize the criteria. Here is a proposed list:
*   **Cost Efficiency (Tokens):** Measured in API token spend per document.
*   **Speed (Latency):** Time taken to generate the full graph.
*   **Reproducibility (Determinism):** Does it produce the exact same graph if run twice?
*   **Provenance (Auditability):** Can every edge be mathematically traced to a specific source sentence?
*   **Setup Time / Cold Start:** How long does it take to define the schema?

## 3. The Scoring System
Each cell in the matrix will contain a specific UI badge:
*   🟢 **Best (3 points)**
*   🟡 **Good/Acceptable (2 points)**
*   🔴 **Poor/Fails (1 point)**

### Proposed Matrix Data

| Criteria | KGSpin | LLM Full-Shot | LLM Multi-Stage |
| :--- | :--- | :--- | :--- |
| **Cost** | 🟢 Zero Tokens | 🔴 Highest Tokens | 🟡 High Tokens |
| **Speed** | 🟢 Seconds | 🟢 Seconds | 🔴 Minutes |
| **Reproducibility** | 🟢 100% Deterministic | 🔴 Stochastic (Varies) | 🔴 Stochastic (Varies) |
| **Provenance** | 🟢 Exact Lineage | 🔴 Hallucination Risk | 🟡 Moderate Traceability |
| **Schema Setup** | 🟡 YAML Compilation (Mins) | 🟢 Natural Language (Instant) | 🔴 Complex Prompt Engineering |

## 4. The "Winner" Calculation
The UI will visually sum the scores at the bottom of each column.
*   **KGSpin:** 14 points (Winner - Crown Icon)
*   **LLM Multi-Stage:** 8 points
*   **LLM Full-Shot:** 8 points

*The UI should automatically highlight the winning column (KGSpin) in our brand accent color (#5ED68A).*

## 5. Open Questions for Product

Before engineering begins, we need to decide on the following:
1.  **Dynamic vs. Static:** Should the scores be hardcoded based on our assertions, or should some of them (like Speed and Cost) dynamically bind to the *actual* run results from the demo (e.g., if the user runs a massive 10-K, the speed metric actually reads the exact ms duration)?
2.  **Weighting:** Are all criteria weighted equally (1-3 points)? Or is Reproducibility a "10x multiplier" because it's a hard enterprise requirement?
3.  **Visual Language:** Do we want text inside the matrix boxes (e.g., "Zero Tokens"), or just icons (🟢 / 🔴) with tooltips to keep it visually cleaner?
