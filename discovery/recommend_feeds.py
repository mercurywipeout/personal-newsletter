#!/usr/bin/env python3
"""
Automatic feed recommendation system.

Discovers candidate RSS/Atom feeds by analysing outbound links from articles
already in the newsletter pipeline, validates them, and writes ranked
recommendations to data/feed_recommendations.json.

Public API
----------
discover_candidate_domains(stories, max_stories, per_story_max_links)
    → dict[domain, list[provenance]]

find_feeds_for_domain(domain) → list[str]

validate_feed(feed_url) → dict

score_candidate(domain, feed_url, validation, provenance) → (float, list[str])

run_recommendations(stories, day, auto_add) → list[dict]

save_recommendations(recs) → None

CLI
---
python -m discovery.recommend_feeds [--day YYYY-MM-DD] [--auto-add]
"""

import argparse
import json
import re
import time
import urllib3
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE         = Path(__file__).parent.parent
DATA_DIR      = _HERE / "data"
CLUSTERS_DIR  = DATA_DIR / "clusters"
RECS_PATH     = DATA_DIR / "feed_recommendations.json"

# ── Tuning constants ───────────────────────────────────────────────────────────
MAX_STORY_PAGES_FETCHED         = 25
PER_STORY_MAX_LINKS             = 25
MAX_CANDIDATE_DOMAINS           = 50
MAX_FEED_URLS_TESTED_PER_DOMAIN = 8
GLOBAL_TIME_BUDGET_SECS         = 120   # hard wall-clock cap per run
DOMAIN_RATE_LIMIT_SECS          = 1.0   # min gap between requests to same domain

AUTO_ADD_RECOMMENDED_FEEDS = False   # override with env AUTO_ADD_RECOMMENDED_FEEDS=1
AUTO_ADD_MAX_PER_WEEK      = 3
AUTO_ADD_MIN_SCORE         = 7.0

# ── Domain block-lists ─────────────────────────────────────────────────────────
_BLOCKED_DOMAINS = {
    # Social media
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "tiktok.com",
    "reddit.com", "redditinc.com", "youtube.com", "youtu.be", "instagram.com",
    "pinterest.com", "tumblr.com", "snapchat.com", "threads.net", "bsky.app",
    "mastodon.social",
    # Code hosting / GitHub properties (GitHub Blog is manually curated)
    "github.com", "github.io", "github.blog", "gitlab.com", "bitbucket.org",
    "atom.io",  # redirects to github.blog
    # Big tech / app stores
    "amazon.com", "amzn.to", "google.com", "googleapis.com",
    "apple.com", "apps.apple.com", "microsoft.com",
    # Reference / encyclopedias
    "wikipedia.org", "en.wikipedia.org", "wikimedia.org", "wikidata.org",
    "wikibooks.org", "wikiquote.org",
    # Archive / cache services
    "archive.org", "archive.ph", "web.archive.org", "webcache.googleusercontent.com",
    # Link shorteners / redirects
    "bit.ly", "tinyurl.com", "t.co", "ow.ly", "buff.ly",
    # CDNs / infrastructure
    "cloudflare.com", "cloudfront.net", "akamai.com",
    # News aggregators (we are one)
    "news.google.com", "feedly.com", "flipboard.com",
}
_TRACKING_KEYWORDS = ("tracking", "affiliate", "clicks.", "analytics", ".pixel",
                      "stats.", "beacon.", "doubleclick")

# ── Topical relevance keywords ─────────────────────────────────────────────────
_TECH_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "language model", "llm", "neural", "nlp", "gpt", "transformer",
    "openai", "anthropic", "tech", "technology", "software", "developer",
    "engineering", "startup", "computing", "robotics", "research",
    "data science", "api", "cloud", "security", "algorithm", "programming",
    "open source", "python", "javascript", "model", "benchmark", "inference",
    "training", "fine-tuning", "rag", "agent", "autonomous",
}

_OFF_TOPIC_KEYWORDS = {
    "museum", "tourism", "travel", "sport", "sports", "fitness", "recipe",
    "cooking", "food", "fashion", "beauty", "wellness", "wildlife",
    "gardening", "archaeology", "astrology", "horoscope",
}

# ── Common feed paths to probe when homepage discovery fails ───────────────────
_COMMON_FEED_PATHS = [
    "/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
    "/index.xml", "/blog/feed", "/blog/rss.xml", "/rss/",
]

# ── HTTP pool (separate from article_fetcher) ──────────────────────────────────
_http = urllib3.PoolManager(
    timeout=urllib3.Timeout(connect=8, read=12),
    headers={
        "User-Agent":      "RSS-Discovery/1.0 (newsletter feed scout)",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    },
    num_pools=5,
)


# ── Pure domain helpers ────────────────────────────────────────────────────────
def _normalized_domain(url: str) -> str:
    """Return lowercased netloc with leading 'www.' stripped."""
    try:
        return urlparse(url).netloc.lower().lstrip("www.").split(":")[0]
    except Exception:
        return ""


def _is_blocked_domain(domain: str) -> bool:
    """Return True for social, tracking, and other non-article domains."""
    if domain in _BLOCKED_DOMAINS:
        return True
    if any(kw in domain for kw in _TRACKING_KEYWORDS):
        return True
    return False


# ── Pure HTML extraction helpers (no I/O; unit-testable with fixture strings) ──

class _LinkParser(HTMLParser):
    """Extract absolute href + anchor text from <a> tags."""

    def __init__(self, base_url: str):
        super().__init__()
        self._base    = base_url
        self._in_a    = False
        self._buf:   list = []
        self.links:  list = []   # [(abs_url, anchor_text)]

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                abs_url = urljoin(self._base, href)
                self.links.append([abs_url, ""])
                self._in_a = True
                self._buf  = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            self._in_a = False
            if self.links:
                self.links[-1][1] = " ".join(self._buf).strip()[:100]

    def handle_data(self, data):
        if self._in_a:
            self._buf.append(data.strip())


class _FeedLinkParser(HTMLParser):
    """Extract <link rel="alternate"> feed URLs from an HTML head."""

    _FEED_TYPES = {
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
    }

    def __init__(self, base_url: str):
        super().__init__()
        self._base      = base_url
        self.feed_links: list = []   # [(abs_url, feed_type_str)]

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        d = dict(attrs)
        if d.get("rel") == "alternate" and d.get("type") in self._FEED_TYPES:
            href = d.get("href", "").strip()
            if href:
                abs_url = urljoin(self._base, href)
                # map MIME → short label
                t = d["type"]
                if "atom" in t:
                    label = "atom"
                elif "json" in t:
                    label = "json"
                else:
                    label = "rss"
                self.feed_links.append((abs_url, label))


def _extract_links_from_html(html: str, base_url: str) -> list:
    """Return [(abs_url, anchor_text)] from HTML anchor tags."""
    p = _LinkParser(base_url)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.links


def _extract_feed_links_from_html(html: str, base_url: str) -> list:
    """Return [(abs_url, feed_type)] from <link rel='alternate'> tags in HTML."""
    p = _FeedLinkParser(base_url)
    try:
        p.feed(html)
    except Exception:
        pass
    return p.feed_links


# ── Network fetch functions (patchable in tests) ───────────────────────────────

def _fetch_html(url: str, max_bytes: int = 512 * 1024):
    """
    Fetch URL and return (decoded_html, http_status) or (None, error_str).
    Reads at most max_bytes to avoid downloading large non-HTML responses.
    """
    try:
        resp = _http.request("GET", url, redirect=True, preload_content=False)
        status = resp.status
        if status != 200:
            resp.drain_conn()
            return None, status
        data = resp.read(max_bytes)
        resp.close()
        return data.decode("utf-8", errors="replace"), status
    except Exception as exc:
        return None, str(exc)[:200]


def _fetch_bytes(url: str, max_bytes: int = 2 * 1024 * 1024):
    """
    Fetch URL and return (bytes, http_status) or (None, error_str).
    Reads at most max_bytes to cap memory usage for large feeds.
    """
    try:
        resp = _http.request("GET", url, redirect=True, preload_content=False)
        status = resp.status
        if status != 200:
            resp.drain_conn()
            return None, status
        data = resp.read(max_bytes)
        resp.close()
        return data, status
    except Exception as exc:
        return None, str(exc)[:200]


# ── Core discovery ─────────────────────────────────────────────────────────────

def discover_candidate_domains(
    stories: list,
    max_stories: int = MAX_STORY_PAGES_FETCHED,
    per_story_max_links: int = PER_STORY_MAX_LINKS,
) -> dict:
    """
    Fetch story pages, extract outbound links, and return a mapping of
    external domain → list of provenance dicts [{source_url, anchor_text}].

    Respects a per-domain rate limit and a global wall-clock budget.
    Returns at most MAX_CANDIDATE_DOMAINS domains sorted by reference count.
    """
    deadline          = time.monotonic() + GLOBAL_TIME_BUDGET_SECS
    domain_last_fetch: dict = {}          # domain → monotonic time of last fetch
    domain_provenance: dict = {}          # domain → [{source_url, anchor_text}]

    for story in stories[:max_stories]:
        if time.monotonic() > deadline:
            break

        url = story.get("final_url") or story.get("url", "")
        if not url:
            continue

        source_domain = _normalized_domain(url)

        # Per-domain rate limiting for story pages
        now = time.monotonic()
        last = domain_last_fetch.get(source_domain, 0.0)
        wait = DOMAIN_RATE_LIMIT_SECS - (now - last)
        if wait > 0:
            time.sleep(wait)
        domain_last_fetch[source_domain] = time.monotonic()

        html, _ = _fetch_html(url)
        if not html:
            continue

        links = _extract_links_from_html(html, url)
        seen_this_story: set = set()
        link_count = 0

        for abs_url, anchor_text in links:
            if link_count >= per_story_max_links:
                break
            domain = _normalized_domain(abs_url)
            if not domain or domain == source_domain:
                continue
            if _is_blocked_domain(domain):
                continue
            if domain in seen_this_story:
                continue
            seen_this_story.add(domain)
            domain_provenance.setdefault(domain, []).append({
                "source_url":  url,
                "anchor_text": anchor_text,
            })
            link_count += 1

    # Sort by reference count descending, cap to MAX_CANDIDATE_DOMAINS
    ranked = sorted(domain_provenance.items(), key=lambda x: (-len(x[1]), x[0]))
    return dict(ranked[:MAX_CANDIDATE_DOMAINS])


def find_feeds_for_domain(domain: str) -> list:
    """
    Return a list of candidate feed URLs for a domain (up to
    MAX_FEED_URLS_TESTED_PER_DOMAIN), using three strategies:

    1. Fetch homepage and parse <link rel="alternate"> tags.
    2. Probe common feed paths.
    3. Substack heuristic.
    """
    base       = f"https://{domain}"
    candidates = []

    # Strategy 1: homepage autodiscovery
    html, _ = _fetch_html(base)
    if html:
        feed_links = _extract_feed_links_from_html(html, base)
        if feed_links:
            return [url for url, _ in feed_links][:MAX_FEED_URLS_TESTED_PER_DOMAIN]

    # Strategy 3: Substack heuristic (prepend before generic paths)
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] == "substack" and parts[-1] == "com":
        candidates.append(f"https://{domain}/feed")

    # Strategy 2: common feed paths
    for path in _COMMON_FEED_PATHS:
        candidates.append(base + path)

    return candidates[:MAX_FEED_URLS_TESTED_PER_DOMAIN]


def validate_feed(feed_url: str) -> dict:
    """
    Fetch and parse a feed URL. Returns a validation dict:
      {ok, status, feed_type, items_sampled, latest_pub, error}
    """
    result = {
        "ok":                False,
        "status":            None,
        "feed_type":         None,
        "items_sampled":     0,
        "latest_pub":        None,
        "feed_title":        None,
        "feed_description":  None,
        "entry_title_sample": [],
        "error":             None,
    }

    data, status = _fetch_bytes(feed_url)
    result["status"] = status

    if data is None:
        result["error"] = f"fetch_failed:{status}"
        return result

    try:
        parsed = feedparser.parse(data)
    except Exception as exc:
        result["error"] = f"parse_error:{exc}"
        return result

    entries = parsed.get("entries", [])
    valid   = [e for e in entries if e.get("link") and e.get("title")]

    if len(valid) < 3:
        result["error"] = f"too_few_entries:{len(valid)}"
        return result

    # Detect feed type from feedparser version string
    version = (parsed.get("version") or "").lower()
    if "atom" in version:
        result["feed_type"] = "atom"
    elif "json" in version:
        result["feed_type"] = "json"
    else:
        result["feed_type"] = "rss"

    result["ok"]                = True
    result["items_sampled"]     = min(len(valid), 10)
    result["feed_title"]        = (parsed.feed.get("title") or "")[:200]
    result["feed_description"]  = (parsed.feed.get("description")
                                   or parsed.feed.get("subtitle") or "")[:500]
    result["entry_title_sample"] = [
        e["title"] for e in valid[:5] if e.get("title")
    ]

    # Latest publish date
    latest = None
    for entry in valid[:10]:
        for field in ("published_parsed", "updated_parsed"):
            raw = entry.get(field)
            if raw:
                try:
                    pub_str = datetime(*raw[:6]).strftime("%Y-%m-%d")
                    if latest is None or pub_str > latest:
                        latest = pub_str
                except Exception:
                    pass
    result["latest_pub"] = latest
    return result


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_candidate(
    domain: str,
    feed_url: str,
    validation: dict,
    provenance: list,
) -> tuple:
    """
    Compute a recommendation score and human-readable reasons list.
    Returns (score: float, reasons: list[str]).
    """
    if not validation.get("ok"):
        return 0.0, [f"invalid: {validation.get('error', 'unknown')}"]

    score   = 5.0
    reasons = ["valid feed"]

    # Items count
    items = validation.get("items_sampled", 0)
    if items >= 10:
        score += 2.0
        reasons.append(f"{items} items sampled")
    elif items >= 5:
        score += 1.0
        reasons.append(f"{items} items sampled")

    # Recency
    latest = validation.get("latest_pub")
    if latest:
        try:
            age = (datetime.utcnow().date()
                   - datetime.strptime(latest, "%Y-%m-%d").date()).days
            if age <= 7:
                score += 3.0
                reasons.append(f"active ({age}d ago)")
            elif age <= 30:
                score += 1.5
                reasons.append(f"recent ({age}d ago)")
            elif age <= 90:
                score += 0.5
                reasons.append(f"last post {age}d ago")
            else:
                score -= 1.0
                reasons.append(f"stale ({age}d ago)")
        except Exception:
            pass

    # Reference count
    ref_count = len(provenance)
    if ref_count >= 3:
        score += 2.0
        reasons.append(f"referenced from {ref_count} stories")
    elif ref_count >= 2:
        score += 1.0
        reasons.append(f"referenced from {ref_count} stories")

    # Feed path signal
    path = urlparse(feed_url).path.lower()
    if any(kw in path for kw in ("/blog", "/news", "/articles")):
        score += 0.5
        reasons.append("blog/news path")

    # Topical relevance
    corpus = " ".join([
        validation.get("feed_title") or "",
        validation.get("feed_description") or "",
        domain,
        *validation.get("entry_title_sample", []),
    ]).lower()
    if any(kw in corpus for kw in _TECH_KEYWORDS):
        score += 3.0
        reasons.append("tech-relevant")
    elif any(kw in corpus for kw in _OFF_TOPIC_KEYWORDS):
        score -= 4.0
        reasons.append("off-topic")

    return round(score, 2), reasons


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_existing_domains() -> set:
    """Return the set of normalized domains already present in data/feeds.json."""
    from scoring.scorecard import FEEDS_STATE
    if not FEEDS_STATE.exists():
        return set()
    try:
        feeds = json.loads(FEEDS_STATE.read_text(encoding="utf-8"))
        return {_normalized_domain(f.get("url", "")) for f in feeds if f.get("url")}
    except Exception:
        return set()


def save_recommendations(recs: list) -> None:
    """Write recommendations to data/feed_recommendations.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RECS_PATH.write_text(
        json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _append_to_seed(new_entries: list) -> None:
    """Append newly discovered feeds to config/feeds.seed.json."""
    seed_path = _HERE / "config" / "feeds.seed.json"
    try:
        seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception:
        return
    existing_urls = {f.get("url", "") for f in seeds}
    appended = False
    for entry in new_entries:
        if entry.get("url") not in existing_urls:
            seeds.append(entry)
            existing_urls.add(entry["url"])
            appended = True
    if appended:
        seed_path.write_text(
            json.dumps(seeds, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def _auto_add_feeds(recs: list, max_adds: int = AUTO_ADD_MAX_PER_WEEK,
                    min_score: float = AUTO_ADD_MIN_SCORE) -> None:
    """Add top-scoring candidates to data/feeds.json and config/feeds.seed.json."""
    from scoring.scorecard import load_feeds_state, save_feeds_state, slug

    existing    = _load_existing_domains()
    added       = 0
    state       = load_feeds_state()
    seed_batch  = []
    today       = datetime.utcnow().strftime("%Y-%m-%d")

    for rec in recs:
        if added >= max_adds:
            break
        if rec.get("score", 0) < min_score:
            continue
        domain = rec["domain"]
        if domain in existing:
            continue

        feed_id = slug(domain)
        state.append({
            "id":                    feed_id,
            "name":                  domain,
            "url":                   rec["feed_url"],
            "status":                "probation",
            "added_at":              today,
            "consecutive_poor_days": 0,
            "good_days_streak":      0,
            "last_transition":       None,
        })
        seed_batch.append({
            "name":     domain,
            "url":      rec["feed_url"],
            "category": "ai",
            "enabled":  True,
            "priority": 1,
        })
        existing.add(domain)
        added += 1
        print(f"  ✓ Auto-added feed: {domain} (score={rec['score']:.1f})")

    if added:
        save_feeds_state(state)
        _append_to_seed(seed_batch)


def _print_summary(recs: list) -> None:
    valid = [r for r in recs if r.get("validation", {}).get("ok")]
    print(f"\n  Feed Recommendations: {len(recs)} candidates, "
          f"{len(valid)} with valid feeds")
    if not recs:
        return
    print("  Top 10:")
    for i, r in enumerate(recs[:10], 1):
        score   = r.get("score", 0.0)
        reasons = ", ".join(r.get("reasons", []))
        print(f"    {i:2}. [{score:4.1f}] {r['domain']}  —  {reasons}")


# ── Orchestration ──────────────────────────────────────────────────────────────

def run_recommendations(
    stories: list,
    day: str = None,
    auto_add: bool = False,
) -> list:
    """
    Full workflow: discover outbound domains → find feeds → validate → score
    → save to data/feed_recommendations.json → print summary.

    Returns the sorted recommendations list.
    """
    if day is None:
        day = datetime.utcnow().strftime("%Y-%m-%d")

    existing = _load_existing_domains()
    print(f"\n  [Feed Discovery] Analysing {min(len(stories), MAX_STORY_PAGES_FETCHED)}"
          f" stories for new feed candidates…")

    # Step 1: discover candidate domains from outbound links
    candidates = discover_candidate_domains(stories)
    new_domains = {d: p for d, p in candidates.items() if d not in existing}
    print(f"  Found {len(candidates)} external domains, "
          f"{len(new_domains)} not yet in feed list")

    recs      = []
    validated = 0
    deadline  = time.monotonic() + (GLOBAL_TIME_BUDGET_SECS / 2)  # half budget remaining

    # Step 2: for each new domain, find + validate feeds
    for domain, provenance in new_domains.items():
        if time.monotonic() > deadline:
            print("  (time budget reached — stopping early)")
            break

        feed_urls = find_feeds_for_domain(domain)

        for feed_url in feed_urls:
            if time.monotonic() > deadline:
                break
            v = validate_feed(feed_url)
            validated += 1
            if not v.get("ok"):
                continue

            score, reasons = score_candidate(domain, feed_url, v, provenance)
            recs.append({
                "domain":          domain,
                "feed_url":        feed_url,
                "feed_type":       v.get("feed_type", "rss"),
                "discovered_from": provenance[:3],
                "validation":      v,
                "score":           score,
                "reasons":         reasons,
                "created_at":      datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            break   # one valid feed per domain is enough

    print(f"  Tested {validated} feed URLs across {len(new_domains)} domains")
    recs.sort(key=lambda x: -x["score"])
    save_recommendations(recs)
    _print_summary(recs)

    if auto_add:
        _auto_add_feeds(recs)

    return recs


# ── Bootstrap ──────────────────────────────────────────────────────────────────

def bootstrap_feeds_state(seed_path: Path = None) -> None:
    """
    Pre-populate data/feeds.json from config/feeds.seed.json if the runtime
    state file does not yet exist. Called on newsletter startup.
    """
    from scoring.scorecard import FEEDS_STATE, save_feeds_state, slug

    if FEEDS_STATE.exists():
        return

    if seed_path is None:
        seed_path = _HERE / "config" / "feeds.seed.json"
    if not seed_path.exists():
        return

    seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    today = datetime.utcnow().strftime("%Y-%m-%d")
    state = []
    for f in seeds:
        if not f.get("enabled", True):
            continue
        state.append({
            "id":                    slug(f["name"]),
            "name":                  f["name"],
            "url":                   f["url"],
            "status":                "active",
            "added_at":              today,
            "consecutive_poor_days": 0,
            "good_days_streak":      0,
            "last_transition":       None,
        })
    save_feeds_state(state)
    print(f"  Bootstrapped data/feeds.json from seed ({len(state)} feeds)")


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover and recommend new RSS feeds from recently read articles."
    )
    parser.add_argument("--day",      default=None,
                        help="YYYY-MM-DD cluster date to analyse (default: today)")
    parser.add_argument("--auto-add", action="store_true",
                        help="Auto-add top candidates to data/feeds.json as probation feeds")
    args = parser.parse_args()

    day = args.day or datetime.utcnow().strftime("%Y-%m-%d")

    # Load representative story URLs from cluster manifest
    cluster_path = CLUSTERS_DIR / f"{day}.json"
    stories: list = []
    if cluster_path.exists():
        try:
            clusters = json.loads(cluster_path.read_text(encoding="utf-8"))
            for c in clusters:
                url = c.get("representative_article_id")
                if url:
                    stories.append({"url": url})
        except Exception as exc:
            print(f"  ⚠  Could not read cluster manifest: {exc}")
    else:
        print(f"  No cluster manifest found for {day} ({cluster_path}).")
        print("  Run daily_newsletter.py first to generate one, or pass --day YYYY-MM-DD.")
        return

    if not stories:
        print(f"  No representative stories in cluster manifest for {day}.")
        return

    run_recommendations(stories, day=day, auto_add=args.auto_add)


if __name__ == "__main__":
    main()
