#!/usr/bin/env python3
"""
Multi-Corpus Knowledge Graph Demo

Fetches data from multiple sources for a given ticker, extracts knowledge graphs
from each corpus, and generates a unified visualization with edges color-coded
by source.

Usage:
    uv run python demos/extraction/demo_ticker.py --ticker JNJ
    uv run python demos/extraction/demo_ticker.py --ticker JNJ --skip-fetch
    uv run python demos/extraction/demo_ticker.py --ticker AAPL --no-healthcare
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

OUTPUT_BASE = Path(__file__).resolve().parent / "output"
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"

# Pre-import data source modules to avoid thread deadlocks
from kgenskills.data_sources.edgar import EdgarDataSource  # noqa: E402
from kgenskills.data_sources.finance_news import FinanceNewsDataSource  # noqa: E402
from kgenskills.data_sources.healthcare_news import HealthcareNewsDataSource  # noqa: E402
from kgenskills.services.entity_resolution import (  # noqa: E402
    CanonicalEntity,
    JSONFileEntityService,
    RawEntity,
)


def strip_news_metadata_header(text: str) -> str:
    """Strip metadata headers (Title/Source/Published/Author/etc.) from news articles.

    These headers lack sentence-ending punctuation, which breaks the L-Module's
    sentence splitter and prevents relationship extraction.
    """
    lines = text.lstrip().split('\n')
    meta_prefixes = ('Title:', 'Source:', 'Published:', 'Author:',
                     'Category:', 'Tickers:', 'Drugs:', 'Conditions:',
                     'Search-Entity:')

    if not lines or not lines[0].startswith(meta_prefixes):
        return text

    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '' and body_start == 0:
            body_start = i + 1
            break
        if not stripped.startswith(meta_prefixes) and stripped != '':
            body_start = i
            break

    return '\n'.join(lines[body_start:]).strip() or text


# Known tickers for quick demo (avoids EDGAR CIK lookup)
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


def resolve_ticker(ticker: str, company_name: str | None = None) -> dict:
    """Resolve ticker to company info."""
    ticker = ticker.upper()

    if company_name:
        return {"name": company_name, "domain": "financial", "ticker": ticker}

    if ticker in KNOWN_TICKERS:
        info = KNOWN_TICKERS[ticker]
        return {**info, "ticker": ticker}

    # Fallback: use ticker as company name
    print(f"  Warning: Unknown ticker {ticker}, using as company name")
    return {"name": ticker, "domain": "financial", "ticker": ticker}


def fetch_sec_filing(ticker: str, output_dir: Path) -> bool:
    """Fetch 10-K filing from EDGAR or use cache."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for cached filing
    existing = list(output_dir.glob("*.html"))
    if existing:
        print(f"  SEC filing: using cached {existing[0].name}")
        return True

    # Try EDGAR download
    identity = os.environ.get("EDGAR_IDENTITY")
    if not identity:
        print("  SEC filing: EDGAR_IDENTITY not set, skipping live download")
        print("    Set: export EDGAR_IDENTITY='Your Name your.email@domain.com'")
        return False

    try:
        edgar = EdgarDataSource()
        doc = edgar.get_document(ticker, "10-K", prefer_cache=True)
        if doc:
            filing_path = output_dir / f"{ticker}_10K.html"
            with open(filing_path, "w", encoding="utf-8") as f:
                f.write(doc.raw_html)
            print(f"  SEC filing: downloaded {filing_path.name} ({len(doc.raw_html) // 1024}KB)")
            return True
        else:
            print(f"  SEC filing: no 10-K found for {ticker}")
            return False
    except Exception as e:
        print(f"  SEC filing: fetch failed: {e}")
        return False


def fetch_finance_news(
    ticker: str, company_name: str, entity_terms: list[str], output_dir: Path
) -> int:
    """Fetch finance news articles relevant to the ticker and its entities."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for cached articles
    existing = list(output_dir.glob("*.txt"))
    if existing:
        print(f"  Finance news: using {len(existing)} cached articles")
        return len(existing)

    # Build search terms from entity names (lowercase for matching)
    search_terms_lower = [t.lower() for t in entity_terms] if entity_terms else [company_name.lower(), ticker.lower()]

    try:
        source = FinanceNewsDataSource()
        articles = source.get_ticker_news(ticker, days=30, limit=10)

        # Also search by company name for broader coverage
        if len(articles) < 5:
            name_articles = source.search_news(company_name, limit=10)
            seen_links = {a.link for a in articles}
            for a in name_articles:
                if a.link not in seen_links:
                    articles.append(a)
                    seen_links.add(a.link)

        # Filter for articles that mention any of the entity terms
        relevant = []
        for a in articles:
            text_lower = (a.title + " " + a.summary).lower()
            if any(term in text_lower for term in search_terms_lower):
                relevant.append(a)

        # If no relevant articles found, use curated samples instead
        if len(relevant) == 0:
            sample_count = install_sample_articles(ticker, "finance_news", output_dir)
            if sample_count > 0:
                return sample_count
            # Fall back to general news if no samples available
            general = source.get_latest(limit=10)
            relevant = list(general[:10])

        count = 0
        for i, article in enumerate(relevant[:10]):
            text = article.clean_text
            if len(text.strip()) < 50:
                continue
            filepath = output_dir / f"article_{i + 1:03d}.txt"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)
            count += 1

        print(f"  Finance news: saved {count} articles ({len(relevant)} matched entity terms)")
        return count
    except Exception as e:
        print(f"  Finance news: fetch failed: {e}")
        sample_count = install_sample_articles(ticker, "finance_news", output_dir)
        if sample_count > 0:
            return sample_count
        return 0


def fetch_news_with_terms(
    corpus_type: str, new_terms: list[str], output_dir: Path, existing_count: int
) -> int:
    """Second pass: fetch additional news articles using cross-referenced terms.

    Works for both finance_news and healthcare_news by selecting the appropriate source.
    """
    search_terms_lower = [t.lower() for t in new_terms]

    try:
        if corpus_type == "healthcare_news":
            source = HealthcareNewsDataSource()
            articles = source.get_fda_alerts(limit=20)
            articles.extend(source.get_latest(limit=20))
        else:
            source = FinanceNewsDataSource()
            articles = []
            for term in new_terms[:5]:
                articles.extend(source.search_news(term, limit=5))

        # Filter for articles matching the new terms
        matched = []
        seen_links = set()
        for a in articles:
            if a.link in seen_links:
                continue
            seen_links.add(a.link)
            text_lower = (a.title + " " + a.summary).lower()
            if any(t in text_lower for t in search_terms_lower):
                matched.append(a)

        count = 0
        for article in matched[:5]:
            text = article.clean_text
            if len(text.strip()) < 50:
                continue
            idx = existing_count + count + 1
            filepath = output_dir / f"article_{idx:03d}.txt"
            if not filepath.exists():
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(text)
                count += 1

        if count:
            print(f"  {corpus_type} pass 2: added {count} articles from cross-referenced terms")
        return count
    except Exception as e:
        print(f"  {corpus_type} pass 2: {e}")
        return 0


def fetch_healthcare_news(
    ticker: str, company_name: str, entity_terms: list[str], output_dir: Path
) -> int:
    """Fetch healthcare/FDA news articles relevant to the company's entities."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for cached articles
    existing = list(output_dir.glob("*.txt"))
    if existing:
        print(f"  Healthcare news: using {len(existing)} cached articles")
        return len(existing)

    # Build search terms from entity names (lowercase for matching)
    search_terms_lower = [t.lower() for t in entity_terms] if entity_terms else [company_name.lower()]
    # Also add first word of company name (e.g., "johnson" for "Johnson & Johnson")
    first_word = company_name.lower().split()[0] if company_name else ""
    if first_word and len(first_word) > 3 and first_word not in search_terms_lower:
        search_terms_lower.append(first_word)

    try:
        source = HealthcareNewsDataSource()

        # Get FDA alerts + general healthcare news
        articles = source.get_fda_alerts(limit=20)
        articles.extend(source.get_latest(limit=20))

        # Filter for articles mentioning any entity term
        relevant = []
        seen_links = set()
        for article in articles:
            if article.link in seen_links:
                continue
            seen_links.add(article.link)
            text_lower = (article.title + " " + article.summary).lower()
            if any(term in text_lower for term in search_terms_lower):
                relevant.append(article)

        # If no relevant articles, use curated samples
        if len(relevant) == 0:
            sample_count = install_sample_articles(ticker, "healthcare_news", output_dir)
            if sample_count > 0:
                return sample_count
            # Fall back to general FDA news if no samples available
            for article in articles:
                if article.link not in seen_links:
                    relevant.append(article)
                    seen_links.add(article.link)
                if len(relevant) >= 10:
                    break

        count = 0
        for i, article in enumerate(relevant[:10]):
            text = article.clean_text
            if len(text.strip()) < 50:
                continue
            filepath = output_dir / f"article_{i + 1:03d}.txt"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)
            count += 1

        print(f"  Healthcare news: saved {count} articles ({len(relevant)} matched entity terms)")
        return count
    except Exception as e:
        print(f"  Healthcare news: fetch failed: {e}")
        sample_count = install_sample_articles(ticker, "healthcare_news", output_dir)
        if sample_count > 0:
            return sample_count
        return 0


def load_entity_terms(ticker: str) -> list[str]:
    """Load entity names from the H-Module cache to use as search terms for news fetching.

    Returns a list of entity names (e.g., ["Johnson & Johnson", "Joaquin Duato", "Janssen", ...])
    that can be used to find relevant news articles across corpora.
    """
    cache_file = RESOURCES_DIR / f"{ticker}_h_module.json"
    if not cache_file.exists():
        return []

    with open(cache_file) as f:
        h_module = json.load(f)

    terms = []
    # Add main entity
    if h_module.get("main_entity"):
        terms.append(h_module["main_entity"])

    # Add all entity names and their aliases
    for entity in h_module.get("entities", []):
        terms.append(entity["text"])
        for alias in entity.get("aliases", []):
            alias_text = alias.get("identifier", alias) if isinstance(alias, dict) else alias
            if alias_text and len(alias_text) > 2:
                terms.append(alias_text)

    # Add ticker symbol itself
    terms.append(ticker)

    # Deduplicate while preserving order
    seen = set()
    unique_terms = []
    for term in terms:
        if term.lower() not in seen:
            seen.add(term.lower())
            unique_terms.append(term)

    return unique_terms


def extract_terms_from_articles(article_dir: Path) -> list[str]:
    """Scan fetched articles for entity names that could be used as cross-reference search terms.

    Looks for known ticker companies mentioned in article text, plus capitalized
    multi-word names that likely represent entities (companies, people, organizations).
    """
    terms = set()
    for txt_file in article_dir.glob("*.txt"):
        text = txt_file.read_text(encoding="utf-8")
        text_lower = text.lower()

        # Check for known tickers/companies mentioned
        for sym, info in KNOWN_TICKERS.items():
            if info["name"].lower() in text_lower or sym in text:
                terms.add(info["name"])

        # Extract capitalized multi-word phrases (likely entity names)
        # Match "Word Word" patterns like "Goldman Sachs", "Lisa Su", "Food and Drug Administration"
        for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+(?:&\s+|and\s+|of\s+)?[A-Z][a-z]+)+)\b', text):
            phrase = match.group(1)
            # Filter out common non-entity phrases and short matches
            if len(phrase) > 5 and phrase not in {"The Company", "The Corporation", "United States"}:
                terms.add(phrase)

    return list(terms)


def install_sample_articles(ticker: str, corpus_type: str, output_dir: Path) -> int:
    """Install curated sample articles for a ticker when live feeds lack relevant content.

    Looks for files matching {ticker}_{corpus_type}_NNN.txt in the resources directory.
    Returns number of sample articles installed.
    """
    pattern = f"{ticker}_{corpus_type}_*.txt"
    samples = sorted(RESOURCES_DIR.glob(pattern))
    if not samples:
        return 0

    count = 0
    for sample in samples:
        dst = output_dir / f"article_{count + 1:03d}.txt"
        if not dst.exists():
            shutil.copy2(sample, dst)
            count += 1

    if count:
        print(f"  Installed {count} curated sample articles for {ticker} ({corpus_type})")
    return count


# ── Entity-Targeted News Fetching ─────────────────────────────────────────


def get_search_entities(h_module_path: Path, company_name: str) -> list[dict]:
    """Get entities from the H-Module that are worth searching for in news.

    Filters to organizations that aren't the main company or generic entities.
    Returns list of dicts with 'text' and 'entity_type'.
    """
    if not h_module_path.exists():
        return []

    with open(h_module_path) as f:
        h_module = json.load(f)

    skip = {company_name.lower(), "fda", "sec"}
    entities = []
    for entity in h_module.get("entities", []):
        etype = entity.get("entity_type", "")
        name = entity.get("text", "")
        if etype == "ORGANIZATION" and name.lower() not in skip:
            entities.append({"text": name, "entity_type": etype})

    return entities


def score_article_relevancy(text: str, entity_names: list[str]) -> dict:
    """Score how relevant an article is to the 10-K entities."""
    text_lower = text.lower()
    found = [e for e in entity_names if e.lower() in text_lower]
    return {
        "score": len(found),
        "total": len(entity_names),
        "matched_entities": found,
    }


def build_article_coreference(article_title: str, search_entity: str,
                               company_name: str) -> dict:
    """Build coreference map from article metadata.

    If article title mentions the search entity prominently,
    map 'the company' etc. to that entity instead of the main company.
    """
    title_lower = article_title.lower()
    subject = search_entity if search_entity.lower() in title_lower else company_name

    return {
        "we": subject,
        "We": subject,
        "our": subject,
        "Our": subject,
        "the company": subject,
        "The Company": subject,
        "the firm": subject,
        "The Firm": subject,
    }


def fetch_entity_targeted_news(
    company_name: str,
    entity_names: list[str],
    search_entities: list[dict],
    output_dir: Path,
    articles_per_entity: int = 3,
    max_articles: int = 15,
    min_relevancy: int = 2,
) -> int:
    """Fetch full-text news articles about specific entities from the 10-K.

    Uses Google News RSS to find articles, googlenewsdecoder to resolve URLs,
    and trafilatura to extract full article text. Scores each article by how
    many 10-K entities it mentions and skips those below the relevancy threshold.

    Args:
        company_name: Main company name (e.g., "Johnson & Johnson")
        entity_names: All entity names from the H-Module (for relevancy scoring)
        search_entities: Entities to search for (filtered orgs from H-Module)
        output_dir: Directory to save articles
        articles_per_entity: Max articles to fetch per search entity
        max_articles: Total article cap
        min_relevancy: Minimum entity count for an article to be kept
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for cached articles
    existing = list(output_dir.glob("*.txt"))
    if existing:
        print(f"  Entity news: using {len(existing)} cached articles")
        return len(existing)

    try:
        import feedparser
        import trafilatura
        from googlenewsdecoder import new_decoderv1
        from urllib.parse import quote
    except ImportError as e:
        print(f"  Entity news: missing dependency ({e}), using curated samples")
        return install_sample_articles(company_name.split()[0].upper(), "entity_news", output_dir)

    seen_urls = set()
    articles_saved = 0

    for entity_info in search_entities:
        if articles_saved >= max_articles:
            break

        entity_name = entity_info["text"]
        query = quote(f'"{company_name}" "{entity_name}"')
        feed_url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

        try:
            feed = feedparser.parse(feed_url)
        except Exception:
            continue

        entity_articles = 0
        for entry in feed.entries:
            if articles_saved >= max_articles or entity_articles >= articles_per_entity:
                break

            # Decode Google News URL to actual article URL
            try:
                decoded = new_decoderv1(entry.link)
                if not decoded.get("status"):
                    continue
                actual_url = decoded["decoded_url"]
            except Exception:
                continue

            # Skip duplicates
            if actual_url in seen_urls:
                continue
            seen_urls.add(actual_url)

            # Fetch full article text
            try:
                downloaded = trafilatura.fetch_url(actual_url)
                if not downloaded:
                    continue
                text = trafilatura.extract(downloaded, include_comments=False)
                if not text or len(text) < 300:
                    continue
            except Exception:
                continue

            # Score relevancy
            relevancy = score_article_relevancy(text, entity_names)
            if relevancy["score"] < min_relevancy:
                continue

            # Build metadata header
            source_name = ""
            if hasattr(entry, "source") and entry.source:
                source_name = entry.source.get("title", "")
            published = entry.get("published", "")

            header_lines = [
                f"Title: {entry.title.rsplit(' - ', 1)[0].strip()}",
                f"Source: {source_name}",
                f"Published: {published}",
                f"Search-Entity: {entity_name}",
            ]

            full_text = "\n".join(header_lines) + "\n\n" + text

            # Save article
            articles_saved += 1
            entity_articles += 1
            filepath = output_dir / f"article_{articles_saved:03d}.txt"
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(full_text)

            matched = ", ".join(relevancy["matched_entities"][:4])
            print(f"    {filepath.name}: {entity_name} ({relevancy['score']} entities: {matched})")

            # Rate limit
            time.sleep(0.5)

        # Rate limit between entity searches
        time.sleep(0.5)

    if articles_saved == 0:
        print("  Entity news: no articles found via Google News, trying curated samples")
        # Try curated samples for entity_news first, then fall back to finance_news
        sample_count = install_sample_articles(
            company_name.split()[0].upper(), "entity_news", output_dir
        )
        if sample_count == 0:
            sample_count = install_sample_articles(
                company_name.split()[0].upper(), "finance_news", output_dir
            )
        return sample_count

    print(f"  Entity news: saved {articles_saved} full-text articles")
    return articles_saved


def install_h_module_caches(ticker: str, corpus_dirs: list[Path]) -> int:
    """Install H-Module entity cache alongside each document in all corpus directories.

    The H-Module cache contains the entity list and coreference map that the L-Module
    needs to extract relationships. Without it, the extractor falls back to regex
    entity detection which misses most entities.

    For each .txt or .html file, creates a matching .h_module.json with the same stem.
    """
    cache_src = RESOURCES_DIR / f"{ticker}_h_module.json"
    if not cache_src.exists():
        return 0

    count = 0
    for corpus_dir in corpus_dirs:
        if not corpus_dir.exists():
            continue
        for doc_file in sorted(corpus_dir.glob("*.txt")) + sorted(corpus_dir.glob("*.html")):
            cache_dst = corpus_dir / f"{doc_file.stem}.h_module.json"
            if not cache_dst.exists():
                shutil.copy2(cache_src, cache_dst)
                count += 1

    if count:
        print(f"  Installed H-Module entity cache for {count} documents")
    return count


def write_h_module_from_registry(
    service: JSONFileEntityService, doc_file: Path, coreference_map: dict | None = None
) -> None:
    """Write an .h_module.json containing all entities from the registry.

    Used for SEC filings where we want full entity coverage from the canonical registry.
    """
    cache_file = doc_file.parent / f"{doc_file.stem}.h_module.json"
    if cache_file.exists():
        return

    entities = []
    for ce in service.get_all_entities():
        aliases = [{"identifier": a} for a in ce.aliases]
        entities.append({
            "text": ce.canonical_name,
            "entity_type": ce.entity_type,
            "domain_type": ce.domain_type,
            "aliases": aliases,
            "confidence": ce.confidence,
        })

    coref = coreference_map or {}
    # Build default coreference map from seed H-Module
    if not coref and service.get_main_entity():
        main = service.get_main_entity()
        coref = {
            "we": main, "We": main,
            "our": main, "Our": main,
            "the Company": main, "the company": main,
        }

    h_module_data = {
        "main_entity": service.get_main_entity(),
        "main_entity_type": "ORGANIZATION",
        "entities": entities,
        "coreference_map": coref,
        "document_type": "10-K" if doc_file.suffix == ".html" else "news",
        "domain": "healthcare",
        "h_module_version": "v2.0.0",
        "model_used": "entity-registry",
    }

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(h_module_data, f, indent=2)


def build_document_context_from_registry(
    service: JSONFileEntityService,
    raw_entities: list[RawEntity],
    coreference_map: dict,
) -> dict:
    """Build an h_module.json-compatible dict from resolved entities + document coreference.

    Includes all resolved entities (both existing and newly registered) plus
    any seed entities that are commonly associated with the main entity.
    """
    # Start with entities that were discovered in this article
    seen_ids = set()
    entities = []

    for raw in raw_entities:
        resolved = service.resolve(raw.text, raw.entity_type)
        if resolved:
            ce = service.get(resolved.entity_id)
            if ce and ce.entity_id not in seen_ids:
                seen_ids.add(ce.entity_id)
                aliases = [{"identifier": a} for a in ce.aliases]
                entities.append({
                    "text": ce.canonical_name,
                    "entity_type": ce.entity_type,
                    "domain_type": ce.domain_type,
                    "aliases": aliases,
                    "confidence": ce.confidence,
                    "provenance": ce.provenance,
                })

    # Also include all seed entities (they provide the "known universe" for relationships)
    for ce in service.get_entities_by_provenance("seed"):
        if ce.entity_id not in seen_ids:
            seen_ids.add(ce.entity_id)
            aliases = [{"identifier": a} for a in ce.aliases]
            entities.append({
                "text": ce.canonical_name,
                "entity_type": ce.entity_type,
                "domain_type": ce.domain_type,
                "aliases": aliases,
                "confidence": ce.confidence,
                "provenance": ce.provenance,
            })

    return {
        "main_entity": service.get_main_entity(),
        "main_entity_type": "ORGANIZATION",
        "entities": entities,
        "coreference_map": coreference_map,
        "document_type": "news",
        "domain": "healthcare",
        "h_module_version": "v2.0.0",
        "model_used": "entity-registry+discovery",
    }


def generate_resolved_h_modules(
    ticker: str,
    corpus_dirs: list[Path],
    backend_type: str = "gliner",
    auto_register: bool = True,
) -> int:
    """Generate H-Modules with dynamic entity discovery via the entity resolution service.

    Flow:
    1. Bootstrap resolution service from base H-Module (seed entities)
    2. For each news article, run H-Module agent (GLiNER) to discover entities
    3. Resolve each discovered entity against the registry
    4. Auto-register new entities if enabled
    5. Build enriched DocumentContext and write .h_module.json for L-Module
    """
    h_module_path = RESOURCES_DIR / f"{ticker}_h_module.json"
    if not h_module_path.exists():
        print(f"  No base H-Module found at {h_module_path}")
        return 0

    # 1. Bootstrap resolution service
    registry_path = OUTPUT_BASE / ticker / "entity_registry.json"
    service = JSONFileEntityService(registry_path)
    seed_count = service.bootstrap_from_h_module(h_module_path)
    print(f"  Entity registry: {service.entity_count()} canonical entities ({seed_count} new seeds)")

    # Load coreference map from base H-Module (for SEC filing context)
    with open(h_module_path) as f:
        base_coref = json.load(f).get("coreference_map", {})

    # 2. Create H-Module agent for per-article entity discovery
    try:
        from kgenskills.agents.h_module_agent import create_h_module_agent
        agent = create_h_module_agent(domain="healthcare", backend_type=backend_type)
        print(f"  Entity discovery backend: {backend_type}")
    except Exception as e:
        print(f"  Entity discovery unavailable ({e}), using registry-only mode")
        agent = None

    total_generated = 0
    total_new_entities = 0

    for corpus_dir in corpus_dirs:
        if not corpus_dir.exists():
            continue

        if corpus_dir.name == "sec_filing":
            # SEC filing: write h_module from full registry
            for doc in sorted(corpus_dir.glob("*.html")):
                write_h_module_from_registry(service, doc, base_coref)
                total_generated += 1
            continue

        # News articles: discover + resolve + enrich
        articles = sorted(corpus_dir.glob("*.txt"))
        for i, article in enumerate(articles, 1):
            cache_file = corpus_dir / f"{article.stem}.h_module.json"
            if cache_file.exists():
                continue

            if not agent:
                # No discovery agent — use registry-only with per-article coreference
                raw_text = article.read_text(encoding="utf-8")
                search_entity = None
                for line in raw_text.split('\n')[:6]:
                    if line.startswith('Search-Entity:'):
                        search_entity = line.split(':', 1)[1].strip()
                        break
                coref = (build_article_coreference(article.name, search_entity,
                         service.get_main_entity() or "")
                         if search_entity else base_coref)
                write_h_module_from_registry(service, article, coref)
                total_generated += 1
                continue

            # 3. Discover entities in article (strip metadata for cleaner extraction)
            text = strip_news_metadata_header(article.read_text(encoding="utf-8"))
            try:
                result = agent.extract(text, main_entity_hint=service.get_main_entity())
            except Exception as e:
                print(f"    {article.name}: discovery failed ({e}), using registry")
                write_h_module_from_registry(service, article, base_coref)
                total_generated += 1
                continue

            if not result.success:
                write_h_module_from_registry(service, article, base_coref)
                total_generated += 1
                continue

            # 4. Resolve each discovered entity against registry
            raw_entities = [
                RawEntity(
                    text=e.text,
                    entity_type=e.entity_type,
                    confidence=e.confidence,
                    source_document=str(article),
                )
                for e in result.context.entities
            ]
            resolved = service.resolve_batch(raw_entities)

            # 5. Auto-register new entities if enabled
            if auto_register:
                for raw, res in zip(raw_entities, resolved):
                    if res is None and raw.confidence >= 0.5:
                        service.register(CanonicalEntity.from_raw(raw))
                        total_new_entities += 1

            # 6. Build enriched DocumentContext with per-article coreference
            # Parse Search-Entity from metadata to build article-specific coreference
            raw_text = article.read_text(encoding="utf-8")
            search_entity = None
            for line in raw_text.split('\n')[:6]:
                if line.startswith('Search-Entity:'):
                    search_entity = line.split(':', 1)[1].strip()
                    break
            if search_entity:
                doc_coref = build_article_coreference(
                    article.name, search_entity, service.get_main_entity() or "")
            elif result.context.coreference_map:
                doc_coref = result.context.coreference_map
            else:
                doc_coref = base_coref
            enriched = build_document_context_from_registry(service, raw_entities, doc_coref)

            # 7. Write .h_module.json for L-Module consumption
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(enriched, f, indent=2)
            total_generated += 1

            n_discovered = len(result.context.entities)
            n_resolved = sum(1 for r in resolved if r is not None)
            n_new = n_discovered - n_resolved
            if n_new > 0:
                print(f"    {article.name}: {n_discovered} entities discovered, {n_new} NEW registered")

    if total_new_entities:
        print(f"  Registry grew: {service.entity_count()} entities (+{total_new_entities} auto-discovered)")
    print(f"  Generated {total_generated} H-Modules")
    return total_generated


def extract_corpus(corpus_dir: Path, company_name: str) -> dict:
    """Run KG extraction on a corpus directory."""
    txt_files = list(corpus_dir.glob("*.txt")) + list(corpus_dir.glob("*.html"))
    if not txt_files:
        return {"entities": 0, "relationships": 0, "corpus": corpus_dir.name}

    cmd = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "kgen_cli.py"), "extract",
        "--input", str(corpus_dir),
        "--output", str(corpus_dir),
        "--bundle", str(PROJECT_ROOT / ".bundles" / "financial-v1.1.0"),
        "--main-entity", company_name,
        "--accept-medium-confidence",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=300,
    )

    # Count results from kg.json files
    total_ent = 0
    total_rel = 0
    for kg_file in corpus_dir.glob("*.kg.json"):
        with open(kg_file) as f:
            data = json.load(f)
        total_ent += len(data.get("entities", []))
        total_rel += len(data.get("relationships", []))

    return {
        "entities": total_ent,
        "relationships": total_rel,
        "corpus": corpus_dir.name,
        "returncode": result.returncode,
    }


def generate_visualization(ticker_dir: Path, title: str) -> Path:
    """Generate multi-corpus visualization."""
    output_html = ticker_dir / "graph.html"

    cmd = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "kgen_cli.py"), "visualize",
        "--input", str(ticker_dir),
        "--output", str(output_html),
        "--title", title,
        "--color-by-source",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=60,
    )

    if result.returncode == 0:
        print(f"\n  Visualization: {output_html}")
    else:
        print(f"\n  Visualization failed: {result.stderr}")

    return output_html


def main():
    parser = argparse.ArgumentParser(
        description="Multi-corpus knowledge graph demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python demos/extraction/demo_ticker.py --ticker JNJ
  uv run python demos/extraction/demo_ticker.py --ticker AAPL --no-healthcare
  uv run python demos/extraction/demo_ticker.py --ticker JNJ --skip-fetch
        """,
    )
    parser.add_argument("--ticker", "-t", required=True, help="Stock ticker symbol (e.g., JNJ)")
    parser.add_argument("--company-name", help="Override company name")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip data fetching, use cached files")
    parser.add_argument("--no-open", action="store_true", help="Don't open visualization in browser")
    parser.add_argument("--backend", choices=["gliner", "anthropic", "gemini", "ollama"],
                        default="gliner", help="Backend for article entity discovery (default: gliner)")
    parser.add_argument("--static-h-module", action="store_true",
                        help="Use static H-Module copy instead of dynamic discovery")
    parser.add_argument("--auto-register", action="store_true", default=True,
                        help="Auto-register new entities discovered by H-Module (default: True)")
    parser.add_argument("--no-auto-register", dest="auto_register", action="store_false",
                        help="Only resolve against existing registry, never create new entities")
    parser.add_argument("--articles-per-entity", type=int, default=3,
                        help="Max articles to fetch per search entity (default: 3)")
    parser.add_argument("--max-articles", type=int, default=15,
                        help="Total article cap (default: 15)")
    parser.add_argument("--min-relevancy", type=int, default=2,
                        help="Min 10-K entities an article must mention to be kept (default: 2)")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    info = resolve_ticker(ticker, args.company_name)
    company_name = info["name"]
    domain = info["domain"]

    ticker_dir = OUTPUT_BASE / ticker
    sec_dir = ticker_dir / "sec_filing"
    entity_news_dir = ticker_dir / "entity_news"
    h_module_path = RESOURCES_DIR / f"{ticker}_h_module.json"

    print(f"{'=' * 60}")
    print(f"Multi-Corpus KG Demo: {ticker} ({company_name})")
    print(f"{'=' * 60}")
    print(f"Domain: {domain}")
    print(f"Output: {ticker_dir}")
    print()

    # ── Step 0: Load entity terms from H-Module cache ─────────────
    entity_terms = load_entity_terms(ticker)
    if entity_terms:
        print(f"Entity terms: {', '.join(entity_terms[:8])}{'...' if len(entity_terms) > 8 else ''}")
        print()

    # ── Step 1: Fetch data ────────────────────────────────────────
    # Pipeline: 10-K first, then entity-targeted news search using
    # key entities from the 10-K H-Module. Full article text via trafilatura.
    if not args.skip_fetch:
        print("Step 1: Fetching data...")
        start = time.time()

        # 1a. SEC filing
        fetch_sec_filing(ticker, sec_dir)

        # 1b. Entity-targeted news: search Google News for articles about
        #     specific entities from the 10-K (Shockwave, Kenvue, Abiomed, etc.)
        search_entities = get_search_entities(h_module_path, company_name)
        if search_entities:
            entity_names_for_scoring = [e["text"] for e in
                                        json.load(open(h_module_path)).get("entities", [])] if h_module_path.exists() else []
            print(f"  Searching for: {', '.join(e['text'] for e in search_entities[:6])}{'...' if len(search_entities) > 6 else ''}")
            fetch_entity_targeted_news(
                company_name=company_name,
                entity_names=entity_names_for_scoring,
                search_entities=search_entities,
                output_dir=entity_news_dir,
                articles_per_entity=args.articles_per_entity,
                max_articles=args.max_articles,
                min_relevancy=args.min_relevancy,
            )
        else:
            print("  No search entities found in H-Module, skipping news fetch")

        elapsed = time.time() - start
        print(f"  Fetch complete ({elapsed:.1f}s)")
    else:
        print("Step 1: Using cached data (--skip-fetch)")

    print()

    # ── Step 1b: Generate H-Module entity caches ────────────────────
    # The L-Module needs entity context to extract relationships.
    # Dynamic mode: run H-Module agent per article, resolve against entity registry.
    # Static mode: copy base H-Module to all docs (legacy behavior).
    corpus_dirs = [d for d in [sec_dir, entity_news_dir] if d.exists()]
    if args.static_h_module:
        install_h_module_caches(ticker, corpus_dirs)
    else:
        generate_resolved_h_modules(ticker, corpus_dirs, args.backend, args.auto_register)

    # ── Step 2: Extract KGs ─────────────────────────────────────
    print("Step 2: Extracting knowledge graphs...")
    start = time.time()

    corpora = []
    if sec_dir.exists() and (list(sec_dir.glob("*.html")) or list(sec_dir.glob("*.txt"))):
        corpora.append(("sec_filing", sec_dir))
    if entity_news_dir.exists() and list(entity_news_dir.glob("*.txt")):
        corpora.append(("entity_news", entity_news_dir))

    if not corpora:
        print("  No data to extract from. Run without --skip-fetch first.")
        return 1

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_map = {}
        for name, corpus_dir in corpora:
            f = pool.submit(extract_corpus, corpus_dir, company_name)
            future_map[f] = name

        for future in as_completed(future_map):
            name = future_map[future]
            try:
                result = future.result()
                results[name] = result
                print(f"  {name}: {result['entities']} entities, {result['relationships']} relationships")
            except Exception as e:
                print(f"  {name}: extraction failed: {e}")

    elapsed = time.time() - start
    print(f"  Extraction complete ({elapsed:.1f}s)")
    print()

    # ── Step 3: Summary ─────────────────────────────────────────
    total_ent = sum(r["entities"] for r in results.values())
    total_rel = sum(r["relationships"] for r in results.values())
    print(f"Summary: {total_ent} entities, {total_rel} relationships across {len(results)} corpora")

    # ── Step 4: Visualization ───────────────────────────────────
    print()
    print("Step 3: Generating visualization...")
    graph_path = generate_visualization(ticker_dir, f"{ticker} ({company_name})")

    if graph_path.exists() and not args.no_open:
        if sys.platform == "darwin":
            subprocess.run(["open", str(graph_path)])
        elif sys.platform == "linux":
            subprocess.run(["xdg-open", str(graph_path)])

    print()
    print(f"Done! Open {graph_path} to explore the multi-corpus knowledge graph.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
