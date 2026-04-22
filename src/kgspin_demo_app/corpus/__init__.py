"""kgspin_demo_app.corpus — deprecated in Sprint 09, retained for cross-repo compat.

Pre-Sprint-09 this package hosted the demo's consumers of the retired
``kgspin_core.corpus.CorpusProvider`` Protocol (removed in REQ-005).
Sprint 09 (REQ-007) moves demo to ``kgspin_interface.DocumentFetcher``
landers (see ``src/kgspin_demo_app/landers/``) + admin's HTTP registry as
the authoritative read path.

Surviving contents (scheduled for removal in a post-Sprint-09
Hardening sprint):
- ``mock_provider.MockDocumentFetcher`` — fixture-backed DocumentFetcher
  kept per VP Eng directive (cross-repo consumers unverified).
- ``CorpusFetchError`` / ``ProviderConfigurationError`` — still useful
  as structured-error envelopes for in-process error surfaces; keep
  for now.

``filestore_reader`` was deleted unconditionally (admin's
``ResourceRegistryClient.list(CORPUS_DOCUMENT)`` is the only legitimate
read path now — see REQ-005).
"""

from __future__ import annotations


class ProviderConfigurationError(Exception):
    """Pre-Sprint-09 structured-config-error envelope.

    Raised by callers when a fetcher is available but missing required
    configuration (e.g., a news provider registered but the API key
    env var is unset). Distinct from ``CorpusFetchError`` because the
    provider registration itself is fine — only the runtime config is
    missing.
    """

    def __init__(self, provider_id: str, missing_var: str, hint: str = "") -> None:
        self.provider_id = provider_id
        self.missing_var = missing_var
        self.hint = hint or f"Set {missing_var} to enable {provider_id}."
        super().__init__(f"[{provider_id}] missing {missing_var}: {self.hint}")


class CorpusFetchError(Exception):
    """Pre-Sprint-09 structured-fetch-error envelope.

    Legacy — Sprint 09 landers use ``kgspin_interface.FetcherError``
    and ``FetcherNotFoundError`` directly. Kept here only as a
    compatibility shim for any remaining call sites.

    Wave A: ``doc_id`` replaces the pre-Wave-A ``ticker`` field; the
    identifier is domain-neutral now (SEC tickers, NCT ids, etc.).
    """

    def __init__(
        self,
        doc_id: str,
        reason: str,
        actionable_hint: str,
        attempted: list[str] | None = None,
    ) -> None:
        self.doc_id = doc_id
        self.reason = reason
        self.actionable_hint = actionable_hint
        self.attempted = attempted or []
        super().__init__(f"[{doc_id}] {reason}: {actionable_hint}")


__all__ = ["CorpusFetchError", "ProviderConfigurationError"]
