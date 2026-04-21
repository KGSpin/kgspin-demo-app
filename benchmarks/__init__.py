"""KGSpin benchmark harness — two-arm like-for-like graph comparison.

Track 3 of the alpha MVP: freeze a 10-K corpus + multi-hop question set,
build a graph under two different extractors (Arm A: KGSpin-tuned via
alpha_runner; Arm B: Gemini per-chunk LLM extraction), and score the
same retrieval strategies against both. See ``benchmarks/README.md``.
"""
