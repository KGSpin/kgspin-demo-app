"""Retrieval strategies for the benchmark harness.

Each module exposes ``retrieve(graph, question, top_k=5) -> list[str]``
so ``harness/run.py`` can sweep across strategies with a single contract.
"""
