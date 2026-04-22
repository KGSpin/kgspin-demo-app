"""Per-pipeline extraction dispatchers used by the demo's SSE orchestrators.

Each function here wraps ``KnowledgeGraphExtractor.run_pipeline`` with the
demo's backend-resolution + bundle-resolution shims, and returns a
tuple the compare/refresh/clinical orchestrators can unpack.

Wave B contract: every LLM dispatcher returns a 5-tuple
``(kg_dict, h_tokens, l_tokens, elapsed_seconds, error_count)`` or a
5-tuple ``(kg_dict, tokens, elapsed_seconds, error_count, truncated)``
for the single-prompt (flash) variants. The financial-vs-clinical
arity drift the audit flagged (4-tuple vs 5-tuple) is resolved here.
"""

from .agentic import _run_agentic_analyst, _run_agentic_flash
from .clinical import _run_clinical_gemini_full_shot, _run_clinical_modular
from .kgen import _run_kgenskills

__all__ = [
    "_run_kgenskills",
    "_run_agentic_flash",
    "_run_agentic_analyst",
    "_run_clinical_gemini_full_shot",
    "_run_clinical_modular",
]
