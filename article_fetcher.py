#!/usr/bin/env python3
"""
Full-article retrieval using Trafilatura.

Two-stage pipeline: fetch → extract.
Results are persisted in a JSONL cache (article_cache.jsonl) so the same
URL is never fetched twice across runs.

Public API
----------
extract_with_trafilatura(url)  → structured result dict
fetch_articles(stories)        → {canonical_url: result, ...}
enrich_stories(stories)        → same list, with 'full_text' added where available
"""

import json
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import trafilatura
from trafilatura.settings import use_config

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_ARTICLES_PER_RUN = 20   # max new fetches per pipeline run
MAX_PER_DOMAIN       = 3    # max new fetches from a single domain per run
MIN_TEXT_LENGTH      = 200  # chars — shorter extractions are treated as failures
FULL_TEXT_TRUNCATE   = 2000 # chars of full text passed through to Claude

CACHE_PATH = Path(__file__).parent / "article_cache.jsonl"

# Trafilatura config: 15-second download timeout
_TRAF_CONFIG = use_config()
_TRAF_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "15")


# ── URL helpers ────────────────────────────────────────────────────────────────
def _canonical(url: str) -> str:
    """Return a canonicalized URL via courlan, falling back to the original."""
    try:
        from courlan import clean_url
        return clean_url(url) or url
    except Exception:
        return url


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


# ── JSONL cache ────────────────────────────────────────────────────────────────
def _load_cache() -> dict:
    """Load all cached results keyed by canonical URL."""
    cache: dict = {}
    if not CACHE_PATH.exists():
        return cache
    for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("url"):
                cache[obj["url"]] = obj
        except Exception:
            pass
    return cache


def _append_cache(result: dict) -> None:
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


# ── Core extraction ────────────────────────────────────────────────────────────
def extract_with_trafilatura(url: str) -> dict:
    """
    Fetch and extract article content from a URL.

    Returns a dict with keys:
        url, final_url, title, author, date, site_name,
        text, language, word_count, status, error

    Never raises — failures are returned as structured error objects.
    """
    canonical = _canonical(url)
    result: dict = {
        "url":        canonical,
        "final_url":  None,
        "title":      None,
        "author":     None,
        "date":       None,
        "site_name":  None,
        "text":       None,
        "language":   None,
        "word_count": None,
        "status":     "error",
        "error":      None,
    }

    try:
        downloaded = trafilatura.fetch_url(canonical, config=_TRAF_CONFIG)
        if not downloaded:
            result["error"] = "fetch_failed"
            return result

        raw = trafilatura.extract(
            downloaded,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            deduplicate=True,
            favor_recall=True,
            config=_TRAF_CONFIG,
        )
        if not raw:
            result["error"] = "extraction_failed"
            return result

        data = json.loads(raw)
        text = (data.get("text") or "").strip()
        if len(text) < MIN_TEXT_LENGTH:
            result["error"] = f"text_too_short:{len(text)}"
            return result

        result.update({
            "final_url":  data.get("url") or canonical,
            "title":      data.get("title"),
            "author":     data.get("author"),
            "date":       data.get("date"),
            "site_name":  data.get("sitename"),
            "text":       text,
            "language":   data.get("language"),
            "word_count": len(text.split()),
            "status":     "ok",
        })

    except Exception as exc:
        result["error"] = str(exc)[:300]

    return result


# ── Orchestration ──────────────────────────────────────────────────────────────
def fetch_articles(stories: list) -> dict:
    """
    Fetch full article text for eligible stories.

    Limits (applied before any fetching begins):
      - At most MAX_ARTICLES_PER_RUN new fetches per run
      - At most MAX_PER_DOMAIN new fetches per domain per run

    Cache hits are included in the returned mapping but do not count
    toward either limit.

    Returns {canonical_url: result_dict, ...} for all processed stories.
    """
    cache = _load_cache()
    results: dict        = {}
    to_fetch: list       = []   # (original_url, canonical_url) pairs
    domain_counts: dict  = defaultdict(int)

    # ── Selection stage (apply limits before fetching) ─────────────────────────
    for story in stories:
        url = story.get("url", "")
        if not url:
            continue
        canon = _canonical(url)

        if canon in cache:
            results[canon] = cache[canon]
            continue

        if len(to_fetch) >= MAX_ARTICLES_PER_RUN:
            continue

        dom = _domain(canon)
        if domain_counts[dom] >= MAX_PER_DOMAIN:
            continue

        domain_counts[dom] += 1
        to_fetch.append((url, canon))

    # ── Fetch stage ────────────────────────────────────────────────────────────
    ok_count  = 0
    err_count = 0
    domain_metrics: dict = defaultdict(lambda: {"ok": 0, "error": 0})

    for _orig_url, canon in to_fetch:
        result = extract_with_trafilatura(canon)
        result["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _append_cache(result)
        results[canon] = result

        dom = _domain(canon)
        if result["status"] == "ok":
            ok_count += 1
            domain_metrics[dom]["ok"] += 1
        else:
            err_count += 1
            domain_metrics[dom]["error"] += 1

    cache_hits = len(results) - len(to_fetch)
    print(f"  Article fetch: {ok_count} ok, {err_count} failed "
          f"({cache_hits} cache hits, {len(to_fetch)} new fetches)")

    return results


def enrich_stories(stories: list) -> list:
    """
    Add 'full_text' to stories where extraction succeeds.
    Mutates the list in place and returns it.
    """
    article_data = fetch_articles(stories)
    for story in stories:
        url   = story.get("url", "")
        canon = _canonical(url)
        result = article_data.get(canon)
        if result and result.get("status") == "ok" and result.get("text"):
            story["full_text"] = result["text"][:FULL_TEXT_TRUNCATE]
    return stories
