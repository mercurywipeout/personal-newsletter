#!/usr/bin/env python3
"""
Story deduplication and clustering.

Algorithm
---------
1. Compute a 64-bit SimHash fingerprint per story (title + summary + first 500
   chars of full_text).
2. Use pairwise Hamming distance + a 72-hour publish-time window to decide
   whether two stories belong to the same cluster.
3. Build clusters via union-find (transitive closure).
4. Select one representative per cluster using `score_for_representative()`.
5. Return the representative list + a cluster manifest for persistence and
   telemetry.

Public API
----------
cluster_stories(stories, similarity_threshold, time_window_hours)
    → (representatives: list, clusters: list[dict])

score_for_representative(story) → float
    Isolated scoring function — higher is better.

save_clusters(clusters, day) → None
    Persist cluster manifest to data/clusters/YYYY-MM-DD.json.

emit_cluster_telemetry(all_stories, representatives, clusters) → None
    Emit story_duplicate / story_first_in_cluster events via scorecard telemetry.
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from scoring.scorecard import emit_telemetry

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR     = Path(__file__).parent.parent / "data"
CLUSTERS_DIR = DATA_DIR / "clusters"

# ── Constants ──────────────────────────────────────────────────────────────────
SIMHASH_BITS         = 64
SIMILARITY_THRESHOLD = 18   # max Hamming distance to consider stories similar
                            # calibrated: same-event ~13 bits, diff-event ~25+ bits
TIME_WINDOW_HOURS    = 72   # max publish-time gap (hours) within a cluster

_PUBLISHED_FMT = "%Y-%m-%d %H:%M"


# ── Text helpers ───────────────────────────────────────────────────────────────
def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_hash(token: str) -> int:
    """Stable 64-bit hash of a single token (SHA-256, first 8 bytes)."""
    return int.from_bytes(
        hashlib.sha256(token.encode("utf-8")).digest()[:8], "big"
    )


def _simhash(text: str) -> int:
    """Compute a 64-bit SimHash fingerprint for `text`."""
    tokens = _normalize_text(text).split()
    if not tokens:
        return 0
    v = [0] * SIMHASH_BITS
    for token in tokens:
        h = _token_hash(token)
        for i in range(SIMHASH_BITS):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    result = 0
    for i in range(SIMHASH_BITS):
        if v[i] > 0:
            result |= (1 << i)
    return result


def _hamming_distance(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")


def _fingerprint(story: dict) -> int:
    """Build a SimHash fingerprint from title + summary.

    full_text is intentionally excluded: it is unavailable for many stories,
    and its presence/absence would skew Hamming distances and prevent valid
    clustering. Title + summary is always present and sufficient for detecting
    same-event coverage.
    """
    parts = [story.get("title", ""), story.get("summary", "")]
    return _simhash(" ".join(p for p in parts if p))


# ── Time helpers ───────────────────────────────────────────────────────────────
def _parse_published(published: str):
    if not published or published == "recent":
        return None
    try:
        return datetime.strptime(published, _PUBLISHED_FMT)
    except ValueError:
        return None


def _within_time_window(a: dict, b: dict, hours: int = TIME_WINDOW_HOURS) -> bool:
    """True if publish timestamps are within `hours` of each other (or either is unknown)."""
    ta = _parse_published(a.get("published", ""))
    tb = _parse_published(b.get("published", ""))
    if ta is None or tb is None:
        return True  # can't determine — assume within window (conservative)
    return abs((ta - tb).total_seconds()) <= hours * 3600


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


# ── Representative scoring ─────────────────────────────────────────────────────
def score_for_representative(story: dict) -> float:
    """
    Score a story for representative selection within its cluster.
    Higher is better. Isolated here so it can be tested and tuned independently.

    Criteria (in priority order):
      1. Successful full-text extraction — large binary bonus.
      2. Higher word count (prefers well-developed articles, capped at 800 words).
      3. Earlier publish time — proxy for originality (up to 5 points over 72 h).
    """
    score = 0.0

    full_text = story.get("full_text") or ""
    if full_text:
        score += 1000.0
        words = len(full_text.split())
        score += min(words / 100.0, 8.0)  # up to 8 pts for ≥ 800 words

    published = story.get("published", "")
    if published and published != "recent":
        try:
            dt = _parse_published(published)
            if dt is not None:
                hours_ago = max(0.0, (datetime.now() - dt).total_seconds() / 3600.0)
                score += min(hours_ago / TIME_WINDOW_HOURS * 5.0, 5.0)  # up to 5 pts
        except Exception:
            pass

    return score


# ── Union-Find ─────────────────────────────────────────────────────────────────
class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        px, py = self.find(x), self.find(y)
        if px != py:
            self.parent[px] = py


# ── Cluster ID ─────────────────────────────────────────────────────────────────
def _cluster_id(representative: dict) -> str:
    url = (representative.get("url") or "").encode("utf-8")
    return hashlib.sha256(url).hexdigest()[:12]


# ── Core clustering ────────────────────────────────────────────────────────────
def cluster_stories(
    stories: list,
    similarity_threshold: int = SIMILARITY_THRESHOLD,
    time_window_hours: int = TIME_WINDOW_HOURS,
) -> tuple:
    """
    Cluster stories by content similarity and publish-time proximity.

    Two stories are grouped when:
      - Hamming distance of their SimHash fingerprints ≤ similarity_threshold, AND
      - Their publish timestamps are within time_window_hours of each other.

    Clusters are built via union-find (transitive closure), so if A≈B and B≈C,
    then A, B, C form one cluster even if A and C are not directly similar.

    Returns:
        representatives : list of one story per cluster (the best per score_for_representative)
        clusters        : list of cluster dicts suitable for persistence
    """
    n = len(stories)
    if n == 0:
        return [], []

    fingerprints = [_fingerprint(s) for s in stories]
    uf = _UnionFind(n)

    for i in range(n):
        for j in range(i + 1, n):
            if (_hamming_distance(fingerprints[i], fingerprints[j]) <= similarity_threshold
                    and _within_time_window(stories[i], stories[j], time_window_hours)):
                uf.union(i, j)

    # Group indices by cluster root
    groups: dict = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)

    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    representatives = []
    clusters = []

    for indices in groups.values():
        cluster_stories_list = [stories[i] for i in indices]
        representative = max(cluster_stories_list, key=score_for_representative)

        article_ids = [s.get("url", "") for s in cluster_stories_list]
        domains     = sorted({_domain(s.get("url", "")) for s in cluster_stories_list
                               if s.get("url")})

        clusters.append({
            "cluster_id":               _cluster_id(representative),
            "created_at":               now_str,
            "article_ids":              article_ids,
            "representative_article_id": representative.get("url", ""),
            "domains":                  domains,
        })
        representatives.append(representative)

    return representatives, clusters


# ── Persistence ────────────────────────────────────────────────────────────────
def save_clusters(clusters: list, day: str = None) -> None:
    """Write cluster manifest to data/clusters/YYYY-MM-DD.json."""
    if day is None:
        day = datetime.utcnow().strftime("%Y-%m-%d")
    CLUSTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = CLUSTERS_DIR / f"{day}.json"
    path.write_text(
        json.dumps(clusters, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── Telemetry ──────────────────────────────────────────────────────────────────
def _is_earliest_in_cluster(story: dict, cluster_articles: list) -> bool:
    """True if `story` has the earliest (or tied-earliest) publish time in the cluster."""
    rep_time = _parse_published(story.get("published", ""))
    if rep_time is None:
        return False  # undated stories can't claim originality
    for other in cluster_articles:
        if other.get("url") == story.get("url"):
            continue
        other_time = _parse_published(other.get("published", ""))
        if other_time and other_time < rep_time:
            return False
    return True


def emit_cluster_telemetry(
    all_stories: list,
    representatives: list,
    clusters: list,
) -> None:
    """
    Emit per-feed cluster telemetry into the scorecard pipeline.

    Events:
      story_duplicate      — story was in a multi-article cluster but not chosen
                             as representative (counts toward feed dup_rate)
      story_first_in_cluster — representative was the earliest-published in a
                             multi-article cluster (counts toward first_in_cluster_rate)
    """
    story_by_url = {s.get("url"): s for s in all_stories if s.get("url")}
    rep_urls     = {s.get("url") for s in representatives}

    for cluster in clusters:
        article_ids = cluster["article_ids"]
        rep_url     = cluster["representative_article_id"]
        is_multi    = len(article_ids) > 1

        if not is_multi:
            continue  # singleton clusters produce no telemetry

        cluster_articles = [story_by_url[uid] for uid in article_ids if uid in story_by_url]

        for url in article_ids:
            story = story_by_url.get(url)
            if not story:
                continue
            feed_id = story.get("feed_id", "")
            if not feed_id:
                continue

            is_rep = (url == rep_url)

            if not is_rep:
                emit_telemetry({"event": "story_duplicate", "feed_id": feed_id, "url": url})
            else:
                if _is_earliest_in_cluster(story, cluster_articles):
                    emit_telemetry({
                        "event":   "story_first_in_cluster",
                        "feed_id": feed_id,
                        "url":     url,
                    })
