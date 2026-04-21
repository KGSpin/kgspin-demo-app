"""Fetch the 10-K corpus declared in ``manifest.yaml``.

Usage::

    python benchmarks/corpus/fetch.py
    python benchmarks/corpus/fetch.py --only PEPSICO_2022_10K BOEING_2022_10K
    python benchmarks/corpus/fetch.py --update-manifest  # populate sha256=null entries

Downloads are written to ``benchmarks/corpus/pdfs/<doc_name>.pdf``
(gitignored) and verified against the manifest SHA-256 when present.
Missing SHA values are populated with the freshly-downloaded hashes
into ``benchmarks/corpus/manifest.lock.yaml`` (also gitignored) so
subsequent runs are fully reproducible locally even when the manifest
carries ``sha256: null`` for sources that required a browser session.

PDF → text conversion is out of scope here — the harness handles that
on its side. This script just lands the bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _read_manifest(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_one(doc: dict, out_dir: Path, *, timeout: int = DEFAULT_TIMEOUT) -> tuple[str | None, int, str | None]:
    name = doc["doc_name"]
    url = doc["source_url"]
    dest = out_dir / f"{name}.pdf"
    if dest.is_file() and dest.stat().st_size > 1000:
        data = dest.read_bytes()
        return _sha256(data), len(data), None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/pdf,text/html,*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:
        return None, 0, str(e)
    dest.write_bytes(data)
    return _sha256(data), len(data), None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path,
                        default=Path("benchmarks/corpus/manifest.yaml"))
    parser.add_argument("--out-dir", type=Path,
                        default=Path("benchmarks/corpus/pdfs"))
    parser.add_argument("--only", nargs="+", default=None,
                        help="Restrict to these doc_names.")
    parser.add_argument("--update-manifest", action="store_true",
                        help="Write discovered SHAs to manifest.lock.yaml.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")

    manifest = _read_manifest(args.manifest)
    docs = manifest.get("documents") or []
    if args.only:
        wanted = set(args.only)
        docs = [d for d in docs if d["doc_name"] in wanted]

    args.out_dir.mkdir(parents=True, exist_ok=True)

    locked: dict[str, str] = {}
    failures: list[tuple[str, str]] = []

    for doc in docs:
        sha, size, err = fetch_one(doc, args.out_dir)
        if err or not sha:
            failures.append((doc["doc_name"], err or "empty"))
            logger.warning("[FETCH] %s failed: %s", doc["doc_name"], err)
            continue
        expected = doc.get("sha256")
        if expected and expected != sha:
            failures.append((doc["doc_name"], f"sha mismatch (got {sha}, expected {expected})"))
            logger.error("[FETCH] %s sha mismatch (got %s)", doc["doc_name"], sha)
            continue
        locked[doc["doc_name"]] = sha
        logger.info("[FETCH] %s ok (%d bytes)", doc["doc_name"], size)

    if args.update_manifest and locked:
        import yaml
        lock_path = args.manifest.parent / "manifest.lock.yaml"
        lock_path.write_text(yaml.safe_dump({"sha256": locked}, sort_keys=True))
        logger.info("[FETCH] wrote %s", lock_path)

    if failures:
        logger.warning(
            "[FETCH] %d failures: %s", len(failures),
            ", ".join(n for n, _ in failures),
        )
        return 2 if len(failures) == len(docs) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
