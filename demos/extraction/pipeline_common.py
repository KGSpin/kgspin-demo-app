#!/usr/bin/env python3
"""
Shared pipeline logic for KGenSkills demo apps.

Provides common constants, ticker resolution, text cleaning, and chunk selection
used by both demo_compare.py (web) and demo_ticker.py (CLI).
"""

import html
import os
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Sprint 33: Canonical data lake and output roots
DATA_LAKE_ROOT = PROJECT_ROOT / "data" / "corpus"
OUTPUT_ROOT = PROJECT_ROOT / "output"


# --- Known tickers (shared across demos) ---

KNOWN_TICKERS = {
    "JNJ": {"name": "Johnson & Johnson", "domain": "healthcare"},
    "PFE": {"name": "Pfizer Inc.", "domain": "healthcare"},
    "UNH": {"name": "UnitedHealth Group", "domain": "healthcare"},
    "ABT": {"name": "Abbott Laboratories", "domain": "healthcare"},
    "AAPL": {"name": "Apple Inc.", "domain": "technology"},
    "MSFT": {"name": "Microsoft Corporation", "domain": "technology"},
    "GOOGL": {"name": "Alphabet Inc.", "domain": "technology"},
    "AMD": {"name": "Advanced Micro Devices, Inc.", "domain": "technology"},
    "NVDA": {"name": "NVIDIA Corporation", "domain": "technology"},
    "JPM": {"name": "JPMorgan Chase & Co.", "domain": "financial"},
    "GS": {"name": "Goldman Sachs Group", "domain": "financial"},
}

# Coreference tokens that GLiNER commonly extracts as ORGANIZATION entities.
# These are pronouns/references, not real entity names, and should be filtered.
COREFERENCE_TOKENS = {
    "we", "our", "us", "our company", "the company",
    "the corporation", "the registrant", "company we",
    "company we acquired",
}

BUNDLES_DIR = PROJECT_ROOT / ".bundles"
# Sprint 118: Split bundle directories
DOMAIN_BUNDLES_DIR = BUNDLES_DIR / "domains"
DOMAIN_YAMLS_DIR = PROJECT_ROOT / "bundles" / "domains"
# NOTE: these path constants are retained for backwards-compat with
# ancillary tooling (overnight batches, test fixtures). Runtime bundle
# resolution in this module goes through admin — see list_bundles() and
# resolve_* below. No filesystem fallback: admin is the sovereign
# registry for bundle_compiled + bundle_source_yaml. A misconfigured
# admin raises, never silently degrades to disk.

DEFAULT_ADMIN_URL = "http://127.0.0.1:8750"

# Bundle pinning: set KGEN_DEFAULT_BUNDLE env var to override auto-latest.
# Set to empty string to revert to auto-latest behavior.
# Example: KGEN_DEFAULT_BUNDLE=financial-v4.1.0-structural
_DEFAULT_BUNDLE = os.getenv("KGEN_DEFAULT_BUNDLE", "")


def _admin_list(kind: str, **params: str) -> list[dict]:
    """GET admin's ``/resources?kind=<kind>`` and return the resource list.

    Raises on admin-side failure — no silent fallback. Callers get a
    structured ``AdminServiceUnreachableError`` that the demo surfaces
    as a 500 with an actionable operator message.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    from kgspin_core.registry_client import AdminServiceUnreachableError

    qs = urllib.parse.urlencode({"kind": kind, **params})
    url = f"{_admin_url()}/resources?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise AdminServiceUnreachableError(
            f"admin unreachable at {url}: {exc}"
        ) from exc

    items = payload.get("resources") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _bundle_name_from_pointer(resource: dict) -> str:
    """Derive the user-facing bundle name from a ``bundle_compiled`` resource.

    Admin stores pointer at ``<tree>/<bundle-name>/bundle.json``. The
    ``<bundle-name>`` directory (e.g. ``financial-v2``) is what the UI
    shows and what on-disk bundles historically used. Empty string if
    the pointer is malformed.
    """
    pointer = resource.get("pointer") or {}
    value = pointer.get("value", "")
    if not value:
        return ""
    path = Path(value)
    # pointer points at bundle.json; its parent dir carries the bundle name.
    return path.parent.name


def list_bundles(domain: str = "financial") -> list[str]:
    """List compiled bundle names registered in admin for ``domain``.

    Sorted newest-first by admin's registration timestamp (fallback to
    alphabetical). Raises ``AdminServiceUnreachableError`` if admin
    can't be reached — there is no filesystem fallback.
    """
    resources = _admin_list("bundle_compiled", domain=domain)

    def _registered_at(r: dict) -> str:
        prov = r.get("provenance") or {}
        return prov.get("registered_at", "")

    resources.sort(
        key=lambda r: (_registered_at(r), _bundle_name_from_pointer(r)),
        reverse=True,
    )
    return [_bundle_name_from_pointer(r) for r in resources if _bundle_name_from_pointer(r)]


def list_bundle_options(domain: str = "financial") -> dict:
    """Return bundle options for the UI dropdown.

    Admin-only. Raises ``AdminServiceUnreachableError`` if admin is down
    — no silent disk fallback. Output shape preserved for UI code that
    still reads the ``strategies`` / ``linguistics`` / ``bundles`` legacy
    fields (populated empty when only the split format is available).
    """
    import logging as _logging

    _log = _logging.getLogger(__name__)

    # Split-format domains sourced from admin's bundle_compiled registry.
    resources = _admin_list("bundle_compiled", domain=domain)

    domains: list[dict] = []
    for r in resources:
        name = _bundle_name_from_pointer(r)
        if not name:
            continue
        meta = r.get("metadata") or {}
        prov = r.get("provenance") or {}
        domains.append({
            "domain_id": name,
            "version": name,
            "compiled_at": prov.get("registered_at", ""),
            "description": meta.get("description", ""),
        })

    def _version_sort_key(d):
        import re as _re
        m = _re.search(r'(\d+)', d["version"])
        return int(m.group(1)) if m else 0

    domains.sort(key=_version_sort_key, reverse=True)
    _log.info("bundle options source=admin count=%d", len(domains))

    # W1-D (ADR-003 §5): pipelines come from admin's registry, not the
    # filesystem. list_available_pipelines() handles admin-down gracefully
    # by returning [] so the UI shows the empty-state message.
    pipelines = [
        {"pipeline_id": pid, "name": pid, "strategy": pid}
        for pid in list_available_pipelines()
    ]

    default_domain_id = domains[0]["domain_id"] if domains else ""
    default_pipeline_id = pipelines[0]["pipeline_id"] if pipelines else ""

    default_id = _DEFAULT_BUNDLE or default_domain_id

    return {
        "domains": domains,
        "pipelines": pipelines,
        "default_domain_id": default_domain_id,
        "default_pipeline_id": default_pipeline_id,
        "strategies": [],
        "linguistics": [],
        "bundles": [],
        "default_bundle_id": default_id,
    }


def resolve_bundle_path(name: str) -> Path:
    """Resolve a compiled bundle name to its on-disk directory via admin.

    Queries admin for ``bundle_compiled`` resources, matches on the
    pointer's enclosing directory name, and returns that directory.
    Raises ``FileNotFoundError`` if admin has no matching registration.
    """
    # Bundle names encode the domain class prefix (``financial-v2`` →
    # ``financial``). Use the prefix to scope the admin query.
    domain = name.split("-", 1)[0] if "-" in name else name
    resources = _admin_list("bundle_compiled", domain=domain)
    for r in resources:
        if _bundle_name_from_pointer(r) != name:
            continue
        pointer = r.get("pointer") or {}
        value = pointer.get("value", "")
        if not value:
            continue
        return Path(value).parent
    raise FileNotFoundError(
        f"Bundle {name!r} not registered in admin at {_admin_url()}. "
        f"Run `kgspin-admin sync archetypes <blueprint>` to register it."
    )


def resolve_domain_bundle_path(domain_id: str) -> Path:
    """Resolve a split-format domain bundle to its compiled directory via admin.

    Equivalent to ``resolve_bundle_path`` today — kept as a named entry
    point because callers express intent when resolving split bundles.
    """
    return resolve_bundle_path(domain_id)


def resolve_domain_yaml_path(domain_id: str) -> Path:
    """Resolve a domain source YAML via admin's bundle_source_yaml registry.

    Accepts two input shapes:

    - Versioned id (``financial-v2``, ``clinical-v2``): matches by
      pointer filename stem exactly.
    - Bare domain (``financial``, ``clinical``): returns the most
      recently registered YAML for that domain — newest-first by
      ``provenance.registered_at``. Callers that want a specific
      version pass the versioned id.

    Raises ``FileNotFoundError`` if admin has no registration that matches.
    """
    domain = domain_id.split("-", 1)[0] if "-" in domain_id else domain_id
    resources = _admin_list("bundle_source_yaml", domain=domain)

    is_bare_domain = ("-" not in domain_id)
    candidates: list[Path] = []
    for r in resources:
        pointer = r.get("pointer") or {}
        value = pointer.get("value", "")
        if not value:
            continue
        p = Path(value)
        if is_bare_domain:
            # Collect every YAML registered for this domain; we'll pick
            # the newest after the loop.
            candidates.append(p)
        elif p.stem == domain_id:
            # Versioned id — exact filename-stem match wins.
            return p

    if is_bare_domain and candidates:
        # Newest-first via admin's registered_at. Fall back to
        # alphabetical stem (descending) when registered_at is equal.
        def _registered_at(path: Path) -> str:
            for r in resources:
                if (r.get("pointer") or {}).get("value", "") == str(path):
                    return (r.get("provenance") or {}).get("registered_at", "")
            return ""
        candidates.sort(key=lambda p: (_registered_at(p), p.stem), reverse=True)
        return candidates[0]

    raise FileNotFoundError(
        f"Domain YAML {domain_id!r} not registered in admin at {_admin_url()}. "
        f"Run `kgspin-admin sync archetypes <blueprint>` to register it."
    )


def _admin_url() -> str:
    return os.environ.get("KGSPIN_ADMIN_URL", DEFAULT_ADMIN_URL).rstrip("/")


def list_available_pipelines() -> list[str]:
    """List pipeline ids registered in admin.

    Returns ``[]`` on any admin-side failure so callers can show an
    empty-state message instead of crashing. The startup check in
    ``demo_compare`` warns the operator when this returns empty.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = f"{_admin_url()}/resources?kind=pipeline_config"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        return []

    items = payload.get("resources") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []

    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        pid = meta.get("name") or item.get("id")
        if pid:
            ids.append(str(pid))
    return ids


def _resolve_latest_bundle(domain: str = "financial") -> Path:
    """Return the newest compiled bundle directory for ``domain`` via admin."""
    names = list_bundles(domain)
    if not names:
        raise FileNotFoundError(
            f"No {domain!r} bundles registered in admin at {_admin_url()}. "
            f"Run `kgspin-admin sync archetypes <blueprint>` to register them."
        )
    return resolve_bundle_path(names[0])


def _resolve_default_bundle(domain: str = "financial") -> Path:
    """Resolve the default bundle via admin, honoring KGEN_DEFAULT_BUNDLE."""
    if _DEFAULT_BUNDLE:
        return resolve_bundle_path(_DEFAULT_BUNDLE)
    return _resolve_latest_bundle(domain)


# Module-level default bundle resolution: admin-only. If admin is down
# or hasn't registered any financial bundles at import time, the demo
# boots with BUNDLE_PATH=None — the split-bundle path (UI-driven
# domain+pipeline selection) keeps working, and downstream call sites
# that actually require the default bundle raise a clear error.
try:
    BUNDLE_PATH = _resolve_default_bundle("financial")
except (FileNotFoundError, Exception) as _bundle_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "No financial bundle resolved at import — admin at %s reports no "
        "matching bundle_compiled resource. Split-bundle UI paths still "
        "work; endpoints that rely on the module-level default will "
        "raise at call time. Error: %s",
        _admin_url(), _bundle_err,
    )
    BUNDLE_PATH = None  # type: ignore[assignment]
# Wave A: `bundles/legacy/` tree retired. Financial patterns YAML now
# resolves via admin-backed domain YAML lookup; if admin can't supply one,
# the module-level default is None and downstream consumers must pass
# a split-bundle-resolved path explicitly.
try:
    PATTERNS_PATH: Path | None = resolve_domain_yaml_path("financial")
except FileNotFoundError as _patterns_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "No financial patterns YAML resolved at import — admin reports no "
        "matching resource. Split-bundle UI paths still work; endpoints "
        "that rely on the module-level default will raise at call time. "
        "Error: %s",
        _patterns_err,
    )
    PATTERNS_PATH = None


# --- Self-Cleaning Entity Filters ---


def is_proper_noun_by_ratio(entity_text: str, full_text: str) -> bool:
    """Reject entities that appear more often lowercase than capitalized.

    Real named entities (Pfizer, FDA, Seagen) are consistently capitalized.
    Generic nouns (manufacturers, competitors, revenue) appear mostly lowercase.
    The document text itself is the oracle — no blocklist needed.
    """
    lower_form = entity_text.lower().strip()
    if not lower_form:
        return False

    # Find all case-insensitive occurrences in the document
    pattern = re.compile(r'\b' + re.escape(lower_form) + r'\b', re.IGNORECASE)
    matches = list(pattern.finditer(full_text))
    if not matches:
        return True  # Not in document text, keep (safe default)

    # Count capitalized vs lowercase occurrences
    capitalized = sum(1 for m in matches if m.group()[0].isupper())
    total = len(matches)

    # Reject if majority of occurrences are lowercase
    return capitalized > total / 2


def looks_like_proper_noun(text: str) -> bool:
    """Structural check: reject phrases that are linguistically common nouns.

    Rules (no blocklist, purely structural):
    - Reject if starts with lowercase article/possessive (the, our, its, a, an)
    - Reject if entire multi-word phrase is all lowercase
    - Reject if too short (<2 chars) or too long (>80 chars)
    """
    text = text.strip()
    if len(text) < 2 or len(text) > 80:
        return False

    words = text.split()

    # Starts with article/possessive → generic phrase
    if words[0].lower() in ("the", "a", "an", "our", "its", "their", "his", "her", "my"):
        return False

    # All-lowercase multi-word → generic noun phrase
    if len(words) > 1 and text == text.lower():
        return False

    return True


# --- Ticker Resolution ---


def resolve_ticker(ticker: str, company_name: str | None = None) -> dict:
    """Resolve ticker to company info dict with name, domain, ticker, data_path."""
    ticker = ticker.upper()

    if company_name:
        info = {"name": company_name, "domain": "financial", "ticker": ticker}
    elif ticker in KNOWN_TICKERS:
        info = {**KNOWN_TICKERS[ticker], "ticker": ticker}
    else:
        try:
            import edgar

            identity = os.environ.get("EDGAR_IDENTITY", "")
            if identity:
                edgar.set_identity(identity)
            company = edgar.Company(ticker)
            info = {"name": company.name, "domain": "financial", "ticker": ticker}
        except Exception:
            info = {"name": ticker, "domain": "financial", "ticker": ticker}

    # Sprint 33: Hierarchical data path within the data lake
    info["data_path"] = DATA_LAKE_ROOT / info["domain"] / "sec_edgar" / ticker
    return info


# --- Text Cleaning (fallback when EdgarDocument.clean_text unavailable) ---


def strip_ixbrl(html_content: str) -> str:
    """Strip iXBRL tags from SEC filing HTML."""
    text = re.sub(r"<ix:[^>]*>", "", html_content, flags=re.IGNORECASE)
    text = re.sub(r"</ix:[^>]*>", "", text, flags=re.IGNORECASE)
    return text


def html_to_text(html_content: str) -> str:
    """Convert HTML to plain text using BeautifulSoup.

    Sprint 21: Replaces naive regex stripper that missed <td>, <th>, <table>
    tags, causing the 'table smash' effect where 500-row tables became single
    unbroken strings of 18,000+ tokens.

    Inserts newlines before block-level elements (including table cells)
    to preserve document structure. Each table cell becomes its own line,
    allowing the sentencizer to handle them properly.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script/style content entirely
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Insert newlines before block-level elements including table cells
    block_tags = [
        "div", "p", "br", "tr", "td", "th", "li",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "thead", "tbody", "tfoot", "caption",
        "section", "article", "header", "footer", "blockquote",
    ]
    for tag in soup.find_all(block_tags):
        tag.insert_before("\n")

    text = soup.get_text(separator="\n")

    # Normalize whitespace (preserve newlines as structural boundaries)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


# --- Smart Chunk Selection ---


def select_content_chunks(all_chunks: list, max_chunks: int) -> list:
    """Select the most content-rich chunks from a 10-K filing.

    SEC 10-K filings have relationship-rich content spread across multiple
    sections. This function finds key sections and allocates chunks from each:

    - Item 1 (Business): competitors, products, regions, strategy, M&A
    - Item 7 (MD&A): financial relationships, acquisitions, revenue
    - Item 10 (Directors/Officers): executives, leadership

    Uses a two-pass approach: first find section anchors, then score chunks
    within each section for relationship-rich content.

    Falls back to a mid-document window if no sections are found.
    """
    if len(all_chunks) <= max_chunks:
        return all_chunks

    # Skip the first 10% of chunks (cover page + TOC) before searching.
    # The TOC mentions section names as link text which we must skip.
    skip_toc = max(10, len(all_chunks) // 10)

    # Find anchor points for key sections.
    # Use the FIRST match for Item 1/7 but for Item 10 look for the biographical
    # content (names + titles) rather than the signature page.
    section_anchors = {
        "item1": {
            "keywords": ["item 1.", "item\u00a01.", "business\ngeneral", "our business and strategy"],
            "start": None,
        },
        "item7": {
            "keywords": ["management's discussion", "management\u2019s discussion", "item 7."],
            "start": None,
        },
        "item10": {
            "keywords": [
                "directors, executive officers",
                "directors and executive officers",
                "item 10.",
                "item\u00a010.",
            ],
            "start": None,
        },
    }

    for i in range(skip_toc, len(all_chunks)):
        text_lower = all_chunks[i].text.lower()
        for section_id, section in section_anchors.items():
            if section["start"] is None:
                if any(kw in text_lower for kw in section["keywords"]):
                    section["start"] = i

    # For Item 10: verify we found the biographical section, not the signature page.
    # The biographical section has names with ages and career histories ("has served
    # as ... since YYYY"). The signature page just has titles on short lines.
    import re
    if section_anchors["item10"]["start"] is not None:
        item10_start = section_anchors["item10"]["start"]
        item10_chunk_text = all_chunks[item10_start].text
        # Bio sections have age numbers after names (e.g., "Albert Bourla, DVM, Ph.D.  63")
        has_age_pattern = bool(re.search(r'\b\d{2}\s+(?:Chairman|Chief|President|Director|Vice)', item10_chunk_text))
        has_career_history = "has served as" in item10_chunk_text.lower() or "prior to joining" in item10_chunk_text.lower()
        if not has_age_pattern and not has_career_history:
            # Search backwards for the actual biographical section.
            # Keep going backwards to find the EARLIEST bio chunk (bios are sequential).
            best_bio_idx = None
            for i in range(item10_start - 1, skip_toc, -1):
                check_text = all_chunks[i].text
                is_bio = bool(re.search(r'\b\d{2}\s+(?:Chairman|Chief|President|Director|Vice)', check_text))
                is_bio = is_bio or "has served as" in check_text.lower()
                if is_bio:
                    best_bio_idx = i
                elif best_bio_idx is not None:
                    # We've gone past the bio section — stop
                    break
            if best_bio_idx is not None:
                section_anchors["item10"]["start"] = best_bio_idx

    found_sections = {k: v for k, v in section_anchors.items() if v["start"] is not None}

    if not found_sections:
        # Fallback: skip first 20% (boilerplate) and take from there
        start = len(all_chunks) // 5
        end = min(start + max_chunks, len(all_chunks))
        if end - start < max_chunks:
            start = max(0, end - max_chunks)
        return all_chunks[start:end]

    # Allocate chunks across found sections.
    # Reserve ~25% of slots for keyword-scored relationship-rich chunks.
    base_budget = int(max_chunks * 0.75)
    keyword_budget = max_chunks - base_budget

    # Item 1 (Business) is richest for relationships, then Item 7, then Item 10.
    allocation = {"item1": 0.35, "item7": 0.40, "item10": 0.25}
    selected_set = set()
    selected = []

    for section_id in ["item1", "item7", "item10"]:
        if section_id not in found_sections:
            continue
        section_start = found_sections[section_id]["start"]
        # How many chunks to take from this section
        alloc_count = max(3, int(base_budget * allocation[section_id]))
        section_end = min(section_start + alloc_count, len(all_chunks))
        # Don't overlap into next found section
        for other_id, other in found_sections.items():
            if other_id != section_id and other["start"] is not None:
                if other["start"] > section_start:
                    section_end = min(section_end, other["start"])
        for c in all_chunks[section_start:section_end]:
            if c.id not in selected_set:
                selected.append(c)
                selected_set.add(c.id)

    # Add high-value chunks from Item 1 that mention relationship-relevant content.
    # Score each candidate chunk by keyword density and pick the best ones.
    # This catches business development sections (acquisitions, competitors, etc.)
    # that are deep in Item 1 beyond the base allocation window.
    if "item1" in found_sections:
        item1_start = found_sections["item1"]["start"]
        # Search up to Item 7 (or end of doc)
        item1_end = len(all_chunks)
        for other_id, other in found_sections.items():
            if other_id != "item1" and other["start"] is not None:
                if other["start"] > item1_start:
                    item1_end = min(item1_end, other["start"])

        # High-value keywords: prioritize acquisition/competitor mentions over generic
        high_value_kw = ["acqui", "merger", "compet", "rival"]
        medium_value_kw = ["partnership", "collaborat", "joint venture",
                           "strategic alliance", "headquartered",
                           "chief executive", "chairman", "business development"]

        scored = []
        for i in range(item1_start, item1_end):
            c = all_chunks[i]
            if c.id in selected_set:
                continue
            text_lower = c.text.lower()
            score = sum(3 for kw in high_value_kw if kw in text_lower)
            score += sum(1 for kw in medium_value_kw if kw in text_lower)
            if score > 0:
                scored.append((score, i, c))

        # Sort by score descending, take up to budget
        scored.sort(key=lambda x: (-x[0], x[1]))
        for _, _, c in scored:
            if len(selected) >= max_chunks:
                break
            selected.append(c)
            selected_set.add(c.id)

    # If still have room, pad from Item 7
    if len(selected) < max_chunks and "item7" in found_sections:
        item7_start = found_sections["item7"]["start"]
        for i in range(item7_start, len(all_chunks)):
            if len(selected) >= max_chunks:
                break
            c = all_chunks[i]
            if c.id not in selected_set:
                selected.append(c)
                selected_set.add(c.id)

    return selected[:max_chunks]


def select_content_chunks_v2(
    all_chunks,
    max_chunks: int = 10,
    full_document_text: str = "",
    strategy: str = "dense",
):
    """Chunk selection with strategy switch.

    Args:
        all_chunks: All available Chunk objects.
        max_chunks: Maximum number of chunks to select.
        full_document_text: Full document text for truecasing ratio.
        strategy: "multi" (entity density × section weight, Sprint 23),
                  "dense" (entity-density only, Sprint 20),
                  anything else falls back to section-based.

    Returns:
        Selected chunks sorted by document position.
    """
    if strategy == "multi":
        from kgenskills.execution.chunk_selector import select_chunks_multi_objective
        return select_chunks_multi_objective(
            all_chunks, max_chunks, full_document_text=full_document_text,
        )
    elif strategy == "dense":
        from kgenskills.execution.chunk_selector import select_chunks_by_entity_density
        return select_chunks_by_entity_density(
            all_chunks, max_chunks, full_document_text=full_document_text,
        )
    else:
        # Fall back to original section-based selection
        return select_content_chunks(all_chunks, max_chunks)
