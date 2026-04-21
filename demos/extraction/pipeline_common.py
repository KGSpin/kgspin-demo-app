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

# W1-D (ADR-003 §5): pipeline configs are resolved via admin's PipelineResolver.
# The on-disk PIPELINE_CONFIGS_DIR is gone — see resolve_pipeline_config below.
DEFAULT_ADMIN_URL = "http://127.0.0.1:8750"

# Bundle pinning: set KGEN_DEFAULT_BUNDLE env var to override auto-latest.
# Set to empty string to revert to auto-latest behavior.
# Example: KGEN_DEFAULT_BUNDLE=financial-v4.1.0-structural
_DEFAULT_BUNDLE = os.getenv("KGEN_DEFAULT_BUNDLE", "")


def list_bundles(domain: str = "financial") -> list[str]:
    """List available bundle versions for a domain, newest first.

    Discovers both standard bundles (``financial-v1.8.0``) and variant
    bundles (``financial-fast-v1.0.0``). Sorted by compilation date
    (newest first), falling back to alphabetical for bundles without
    a compiled_at timestamp.
    """
    import json as _json

    dirs = [c for c in BUNDLES_DIR.glob(f"{domain}-*") if c.is_dir()]
    # Also search .bundles/domains/ (Sprint 125: new canonical location)
    if DOMAIN_BUNDLES_DIR.is_dir():
        dirs.extend(c for c in DOMAIN_BUNDLES_DIR.glob(f"{domain}-*") if c.is_dir())

    def _compiled_at(d):
        try:
            with open(d / "bundle.json") as f:
                return _json.load(f).get("compiled_at", "")
        except Exception:
            return ""

    dirs.sort(key=lambda d: (_compiled_at(d), d.name), reverse=True)
    return [d.name for d in dirs]


def _registry_bundles_or_none(domain: str) -> list[dict] | None:
    """Sprint 10 Task 5: try reading BUNDLE_COMPILED resources from admin.

    Returns a list of dicts in the ``list_bundle_options`` ``domains``
    schema — ``{domain_id, version, compiled_at, description}`` — when
    at least one compiled bundle is registered for the domain. Returns
    ``None`` on zero results or any registry-side error.

    TODO(sprint-10-task-5): this fallback-only integration ships ahead
    of the archetypes Sprint 02 cutover. Rip-out criteria documented in
    ``docs/sprints/sprint-10/sprint-plan.md#task-5``:
      (a) ``client.list(BUNDLE_COMPILED, domain="financial")`` returns
          >=1 Resource with ``metadata.name == "financial-v2"`` in prod.
      (b) same for ``clinical-v2``.
      (c) ``resolve_pointer(id)`` returns a readable ``FilePointer``.
      (d) archetypes team announces GA via their Sprint 02 dev report.
    """
    try:
        from kgspin_demo_app.registry_http import HttpResourceRegistryClient
        from kgspin_interface.registry_client import ResourceKind
    except Exception:
        return None
    try:
        client = HttpResourceRegistryClient()
        resources = client.list(ResourceKind.BUNDLE_COMPILED, domain=domain)
    except Exception:
        return None
    if not resources:
        return None
    out: list[dict] = []
    for r in resources:
        meta = r.metadata or {}
        out.append({
            "domain_id": meta.get("name") or r.id,
            "version": meta.get("version", ""),
            "compiled_at": (r.provenance.registered_at.isoformat()
                            if r.provenance and r.provenance.registered_at
                            else ""),
            "description": meta.get("description", ""),
        })
    return out


def list_bundle_options(domain: str = "financial") -> dict:
    """Return bundle options for the UI: split domains + pipelines + legacy bundles.

    Sprint 118: Discovers split domain bundles from ``.bundles/domains/`` and
    pipeline configs from ``bundles/pipelines/``. Also includes legacy monolithic
    bundles for backward compatibility.

    Returns::

        {
            "domains": [
                {"domain_id": "financial-v12", "version": "v12", "compiled_at": "..."},
                ...
            ],
            "pipelines": [
                {"pipeline_id": "emergent", "name": "emergent", "strategy": "emergent"},
                ...
            ],
            "default_domain_id": "financial-v12",
            "default_pipeline_id": "emergent",
            # Legacy fields for backward compat
            "strategies": [...],
            "linguistics": [...],
            "bundles": [...],
            "default_bundle_id": "..."
        }
    """
    import json as _json
    import logging as _logging
    import yaml as _yaml

    _log = _logging.getLogger(__name__)

    # --- Sprint 10 Task 5: prefer registry-registered compiled bundles ---
    # [CTO-AMEND-1] Canonical names from kgspin-blueprint are
    # ``financial-v2`` and ``clinical-v2``. Until archetypes Sprint 02
    # publishes compiled bundles, the registry lookup returns zero and
    # we fall through silently to the on-disk scan below.
    registry_domains = _registry_bundles_or_none(domain)

    # --- Sprint 118: Discover split domain bundles ---
    domains = []
    if DOMAIN_BUNDLES_DIR.is_dir():
        for d in DOMAIN_BUNDLES_DIR.glob(f"{domain}-*"):
            if not d.is_dir():
                continue
            try:
                with open(d / "bundle.json") as f:
                    meta = _json.load(f)
            except Exception:
                continue
            version = meta.get("version", d.name)
            compiled_at = meta.get("compiled_at", "")
            domains.append({
                "domain_id": d.name,
                "version": version,
                "compiled_at": compiled_at,
                "description": meta.get("description", ""),
            })
    # Sort: highest version first (numeric sort for vN format)
    def _version_sort_key(d):
        v = d["version"]
        # Extract numeric part from "vN" or "vN.N" format
        import re as _re
        m = _re.search(r'(\d+)', v)
        return int(m.group(1)) if m else 0
    domains.sort(key=_version_sort_key, reverse=True)

    # Sprint 10 Task 5 continued: registry wins when non-empty.
    if registry_domains:
        _log.info("bundle options source=registry count=%d", len(registry_domains))
        domains = registry_domains
    else:
        _log.info("bundle options source=disk count=%d", len(domains))

    # W1-D (ADR-003 §5): pipelines come from admin's registry, not the
    # filesystem. list_available_pipelines() handles admin-down gracefully
    # by returning [] so the UI shows the empty-state message.
    pipelines = [
        {"pipeline_id": pid, "name": pid, "strategy": pid}
        for pid in list_available_pipelines()
    ]

    default_domain_id = domains[0]["domain_id"] if domains else ""
    default_pipeline_id = "emergent" if any(p["pipeline_id"] == "emergent" for p in pipelines) else (pipelines[0]["pipeline_id"] if pipelines else "")

    # --- Legacy: monolithic bundles (backward compat) ---
    dirs = [c for c in BUNDLES_DIR.glob(f"{domain}-*") if c.is_dir()]
    bundles = []
    strategies = set()
    linguistics = set()

    for d in dirs:
        try:
            with open(d / "bundle.json") as f:
                meta = _json.load(f)
        except Exception:
            continue

        strategy = meta.get("execution_strategy", "default")
        linguistic = meta.get("linguistic_schema", "")
        compiled_at = meta.get("compiled_at", "")

        if not linguistic:
            import re as _re
            version = meta.get("version", "")
            linguistic = _re.sub(r"-(structural|emergent|default|fast)$", "", version)

        if not linguistic:
            continue

        strategies.add(strategy)
        linguistics.add(linguistic)
        bundles.append({
            "bundle_id": d.name,
            "strategy": strategy,
            "linguistic": linguistic,
            "compiled_at": compiled_at,
        })

    bundles.sort(key=lambda b: (b["compiled_at"], b["bundle_id"]), reverse=True)

    default_id = ""
    if _DEFAULT_BUNDLE:
        default_id = _DEFAULT_BUNDLE
    elif bundles:
        default_id = bundles[0]["bundle_id"]

    return {
        # Sprint 118: Split format
        "domains": domains,
        "pipelines": pipelines,
        "default_domain_id": default_domain_id,
        "default_pipeline_id": default_pipeline_id,
        # Legacy fields
        "strategies": sorted(strategies),
        "linguistics": sorted(linguistics, reverse=True),
        "bundles": bundles,
        "default_bundle_id": default_id,
    }


def resolve_bundle_path(name: str) -> Path:
    """Resolve a bundle name (e.g. 'financial-v1.3.0') to its full path.

    Sprint 118: Also checks ``.bundles/domains/`` for split domain bundles.
    """
    path = BUNDLES_DIR / name
    if path.is_dir():
        return path
    # Sprint 118: Check split domain bundles directory
    domain_path = DOMAIN_BUNDLES_DIR / name
    if domain_path.is_dir():
        return domain_path
    raise FileNotFoundError(f"Bundle not found: {path}")


def resolve_domain_bundle_path(domain_id: str) -> Path:
    """Resolve a domain bundle (e.g. 'financial-v12') to its compiled path."""
    path = DOMAIN_BUNDLES_DIR / domain_id
    if not path.is_dir():
        raise FileNotFoundError(f"Domain bundle not found: {path}")
    return path


def resolve_domain_yaml_path(domain_id: str) -> Path:
    """Resolve a domain YAML source file (e.g. 'financial-v12' → bundles/domains/financial-v12.yaml).

    Sprint 118: Used by LLM extractors to build prompts with domain-specific
    entity types and relationship patterns.
    """
    path = DOMAIN_YAMLS_DIR / f"{domain_id}.yaml"
    if path.is_file():
        return path
    raise FileNotFoundError(f"Domain YAML not found: {path}")


_pipeline_resolver = None


def _admin_url() -> str:
    return os.environ.get("KGSPIN_ADMIN_URL", DEFAULT_ADMIN_URL).rstrip("/")


def _get_pipeline_resolver():
    """Lazy singleton — defer the kgspin-core import so the demo module
    loads even if W1-B's PipelineResolver hasn't shipped yet. The error
    surfaces at first call site rather than at import time.
    """
    global _pipeline_resolver
    if _pipeline_resolver is None:
        from kgspin_core.execution.pipeline_resolver import PipelineResolver
        _pipeline_resolver = PipelineResolver(admin_url=_admin_url())
    return _pipeline_resolver


def resolve_pipeline_config(pipeline_id: str) -> dict:
    """Resolve pipeline config via admin registry (ADR-003 §5)."""
    return _get_pipeline_resolver().resolve(pipeline_id)


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
    """Find the latest versioned bundle directory for a domain."""
    names = list_bundles(domain)
    if not names:
        raise FileNotFoundError(f"No bundles found matching {domain}-v* in {BUNDLES_DIR}")
    name = names[0]
    # Check domains/ first (canonical location), then root (legacy)
    domain_path = DOMAIN_BUNDLES_DIR / name
    if domain_path.is_dir():
        return domain_path
    return BUNDLES_DIR / name


def _resolve_default_bundle(domain: str = "financial") -> Path:
    """Resolve the default bundle, respecting KGEN_DEFAULT_BUNDLE env var."""
    if _DEFAULT_BUNDLE:
        # Check domains/ first (canonical), then root (legacy)
        domain_pinned = DOMAIN_BUNDLES_DIR / _DEFAULT_BUNDLE
        if domain_pinned.is_dir():
            return domain_pinned
        pinned = BUNDLES_DIR / _DEFAULT_BUNDLE
        if not pinned.is_dir():
            raise FileNotFoundError(
                f"Pinned bundle not found: {_DEFAULT_BUNDLE}. "
                f"Available: {list_bundles(domain)}"
            )
        return pinned
    return _resolve_latest_bundle(domain)


# INIT-001 Sprint 01: Guard module-level bundle resolution so the server can boot
# without compiled bundles on disk. Downstream code that actually uses BUNDLE_PATH
# will fail loudly at call time; that's Sprint 02 scope (bundle/corpus wiring).
try:
    BUNDLE_PATH = _resolve_default_bundle("financial")
except FileNotFoundError as _bundle_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "No financial bundles found at %s — demo will boot without a default bundle. "
        "Extraction endpoints will fail until bundles are compiled (Sprint 02). Error: %s",
        BUNDLES_DIR, _bundle_err,
    )
    BUNDLE_PATH = None  # type: ignore[assignment]
# Prefer tuned YAML if it exists, otherwise fall back to sample patterns
_tuned_yaml = PROJECT_ROOT / "bundles" / "legacy" / "financial-structural-v5.0.yaml"
PATTERNS_PATH = _tuned_yaml if _tuned_yaml.exists() else PROJECT_ROOT / "bundles" / "legacy" / "financial.yaml"


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
