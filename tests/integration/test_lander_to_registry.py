"""Sprint 09 Task 7 — lander → FakeRegistryClient end-to-end.

Confirms the CLI's fetch-then-register sequence works without a live
admin. Uses one SecLander run; the three other landers share the same
post-fetch register path so this test transitively covers them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kgspin_interface.registry_client import ResourceKind
from kgspin_interface.resources import CorpusDocumentMetadata


SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>10-K — Test Co</title>
    <link href="https://www.sec.gov/test-index.htm" />
    <category term="000099999-25-000001" />
    <updated>2025-02-13T00:00:00Z</updated>
  </entry>
</feed>
"""
SAMPLE_FILING = b"<html>10-K body</html>"


class _Resp:
    def __init__(self, *, text=None, content=b"", status=200, headers=None):
        self.text = text or ""
        self._content = content if content else (text.encode() if text else b"")
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, chunk_size=64 * 1024):
        if self._content:
            yield self._content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)


def test_sec_lander_to_fake_registry_end_to_end(tmp_path: Path, monkeypatch) -> None:
    from kgspin_demo_app.landers import sec as sec_mod
    from tests.fakes.registry_client import FakeRegistryClient

    monkeypatch.setenv("SEC_USER_AGENT", "Integration Test test@example.com")

    atom_resp = _Resp(text=SAMPLE_ATOM)
    filing_resp = _Resp(content=SAMPLE_FILING, headers={"ETag": "abc"})
    def fake_get(url, **kw):
        if "browse-edgar" in url:
            return atom_resp
        return filing_resp
    monkeypatch.setattr(sec_mod, "_get_with_retry", fake_get)

    # Step 1: fetch via the lander (typed dual-method path)
    lander = sec_mod.SecLander()
    result = lander.fetch(
        ticker="TST",
        form="10-K",
        output_root=tmp_path / "corpus",
        date="2025-02-13",
    )

    # Step 2: register with the fake registry
    fake = FakeRegistryClient()
    extras = result.metadata
    doc_meta = CorpusDocumentMetadata(
        domain="financial",
        source="sec_edgar",
        identifier={"ticker": "TST", "form": "10-K"},
        fetch_timestamp=datetime.fromisoformat(
            extras["fetch_timestamp_utc"].replace("Z", "+00:00")
        ),
        mime_type="text/html",
        bytes_written=extras.get("bytes_written"),
        etag=extras.get("etag"),
        source_url=extras.get("source_url"),
        source_extras={
            k: v for k, v in extras.items()
            if k not in {"bytes_written", "etag", "source_url"}
        },
    )
    record = fake.register_corpus_document(
        metadata=doc_meta,
        pointer=result.pointer,
        actor="fetcher:sec_edgar",
    )

    # Step 3: the registered record is listable by kind + domain
    listed = fake.list(ResourceKind.CORPUS_DOCUMENT, domain="financial")
    assert len(listed) == 1
    rec = listed[0]
    assert rec.id == record.id
    assert rec.kind == ResourceKind.CORPUS_DOCUMENT
    # pointer.value points at the file on disk
    assert Path(rec.pointer.value).read_bytes() == SAMPLE_FILING
    # provenance records the actor
    assert rec.provenance.registered_by == "fetcher:sec_edgar"


def test_fake_list_filters_by_source() -> None:
    """Sanity: list() filter semantics match admin's documented shape."""
    from tests.fakes.registry_client import FakeRegistryClient
    from kgspin_interface.resources import FilePointer, CorpusDocumentMetadata
    from datetime import datetime, timezone

    fake = FakeRegistryClient()
    # Register one doc, confirm list with matching source returns it
    # and list with non-matching source returns empty.
    fake.register_corpus_document(
        metadata=CorpusDocumentMetadata(
            domain="financial",
            source="sec_edgar",
            identifier={"ticker": "JNJ", "form": "10-K"},
            fetch_timestamp=datetime.now(timezone.utc),
        ),
        pointer=FilePointer(value="/tmp/fake.html"),
        actor="fetcher:sec_edgar",
    )
    assert len(fake.list(ResourceKind.CORPUS_DOCUMENT, source="sec_edgar")) == 1
    assert len(fake.list(ResourceKind.CORPUS_DOCUMENT, source="marketaux")) == 0


def test_no_test_imports_kgspin_admin() -> None:
    """D7 acceptance: no production-import of kgspin_admin in the test
    suite. Test files that reference the module name as a string literal
    (like this one) are excluded via --exclude on the grep; we only care
    that no Python import statement actually pulls the admin package in."""
    import subprocess
    # Use Python ast to look for actual import statements, not string literals.
    import ast
    from pathlib import Path

    for py in Path("tests").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    assert not n.name.startswith("kgspin_admin"), \
                        f"{py}:{node.lineno} imports {n.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("kgspin_admin"):
                    raise AssertionError(
                        f"{py}:{node.lineno} imports from {node.module}"
                    )
