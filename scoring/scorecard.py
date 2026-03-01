#!/usr/bin/env python3
"""
Feed scorecard and auto-pruning system.

Tracks per-feed quality metrics over a 14-day rolling window and
automatically transitions feeds through active → probation → disabled
when quality degrades.

Public API
----------
slug(name)                           → str           URL-safe feed identifier
sync_feeds_state(rss_feeds)          → list          initialise / update feed records
get_rate_limit(status)               → int           per-source story cap by status
emit_telemetry(event)                               append event to today's JSONL run file
post_run(rss_feeds)                                called at end of each newsletter run
print_scorecard()                                  print scorecard table to stdout
compute_rolling_score(feed_id, ...) → (float|None, int)
"""

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR    = Path(__file__).parent.parent / "data"
FEEDS_STATE = DATA_DIR / "feeds.json"
DAILY_STATS = DATA_DIR / "feed_stats_daily.json"
RUNS_DIR    = DATA_DIR / "runs"

# ── Scoring constants ──────────────────────────────────────────────────────────
WINDOW_DAYS            = 14   # rolling window for score computation
MIN_DAYS_FOR_SCORE     = 3    # minimum days of data needed to produce a score
POOR_THRESHOLD         = 0.25  # below this → poor day
GOOD_THRESHOLD         = 0.45  # above this → healthy day

POOR_DAYS_TO_PROBATION = 14   # consecutive poor days (active) → probation
POOR_DAYS_TO_DISABLED  = 14   # consecutive poor days (probation) → disabled
RECOVERY_DAYS          = 7    # consecutive good days → back to active

_STAT_KEYS = ("stories_fetched", "stories_passed",
              "article_ok", "article_error", "article_blocked")


# ── Helpers ────────────────────────────────────────────────────────────────────
def slug(name: str) -> str:
    """Convert a feed name to a URL-safe slug used as the feed's stable ID."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


# ── Feed state I/O ─────────────────────────────────────────────────────────────
def load_feeds_state() -> list:
    if not FEEDS_STATE.exists():
        return []
    try:
        return json.loads(FEEDS_STATE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_feeds_state(state: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FEEDS_STATE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def sync_feeds_state(rss_feeds: list) -> list:
    """
    Ensure every feed in rss_feeds has a state record in data/feeds.json.

    New feeds are added with status='active'. Existing records have their
    name/url kept in sync with the config. Removed feeds are retained for
    historical reference.

    Returns the updated state list.
    """
    state      = load_feeds_state()
    by_id      = {f["id"]: f for f in state}

    for feed in rss_feeds:
        feed_id = slug(feed["name"])
        if feed_id not in by_id:
            by_id[feed_id] = {
                "id":                    feed_id,
                "name":                  feed["name"],
                "url":                   feed["url"],
                "status":                "active",
                "added_at":              _today(),
                "consecutive_poor_days": 0,
                "good_days_streak":      0,
                "last_transition":       None,
            }
        else:
            by_id[feed_id]["name"] = feed["name"]
            by_id[feed_id]["url"]  = feed["url"]

    updated = list(by_id.values())
    save_feeds_state(updated)
    return updated


# ── Rate limits ────────────────────────────────────────────────────────────────
def get_rate_limit(status: str) -> int:
    """Return the per-source story cap for a feed based on its scorecard status."""
    return {"active": 8, "probation": 3, "disabled": 0}.get(status, 8)


# ── Telemetry ──────────────────────────────────────────────────────────────────
def emit_telemetry(event: dict) -> None:
    """Append a telemetry event to today's append-only JSONL run file."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{_today()}.jsonl"
    record = {**event, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Daily stats aggregation ────────────────────────────────────────────────────
def _blank_day_stats() -> dict:
    return {k: 0 for k in _STAT_KEYS}


def aggregate_daily(day: str) -> dict:
    """
    Read the JSONL telemetry for `day` and aggregate per-feed event counts.

    Returns {feed_id: {stories_fetched, stories_passed, article_ok,
                       article_error, article_blocked}}.
    """
    path = RUNS_DIR / f"{day}.jsonl"
    if not path.exists():
        return {}

    event_map = {
        "story_fetched":   "stories_fetched",
        "story_passed":    "stories_passed",
        "article_ok":      "article_ok",
        "article_error":   "article_error",
        "article_blocked": "article_blocked",
    }
    by_feed: dict = defaultdict(lambda: dict(_blank_day_stats()))

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        feed_id = ev.get("feed_id")
        stat    = event_map.get(ev.get("event", ""))
        if feed_id and stat:
            by_feed[feed_id][stat] += 1

    return {k: dict(v) for k, v in by_feed.items()}


def update_daily_stats(day: str) -> dict:
    """Aggregate telemetry for `day` and upsert it into feed_stats_daily.json."""
    if DAILY_STATS.exists():
        try:
            all_stats = json.loads(DAILY_STATS.read_text(encoding="utf-8"))
        except Exception:
            all_stats = {}
    else:
        all_stats = {}

    all_stats[day] = aggregate_daily(day)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_STATS.write_text(
        json.dumps(all_stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return all_stats


def load_daily_stats() -> dict:
    if not DAILY_STATS.exists():
        return {}
    try:
        return json.loads(DAILY_STATS.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Rolling score ──────────────────────────────────────────────────────────────
def compute_rolling_score(
    feed_id: str,
    daily_stats: dict,
    today: str = None,
) -> tuple:
    """
    Compute the WINDOW_DAYS rolling quality score for a feed.

    Score = 0.7 × yield_rate − 0.3 × block_rate
      yield_rate  = stories_passed / max(stories_fetched, 1)
      block_rate  = article_blocked / max(total_article_attempts, 1)

    Returns (score: float | None, days_with_data: int).
    Returns (None, days_with_data) when fewer than MIN_DAYS_FOR_SCORE days
    have data in the window.
    """
    if today is None:
        today = _today()

    window_start = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS - 1)
    ).strftime("%Y-%m-%d")

    totals         = _blank_day_stats()
    days_with_data = 0

    for day, day_data in daily_stats.items():
        if window_start <= day <= today:
            fd = day_data.get(feed_id)
            if fd:
                days_with_data += 1
                for k in _STAT_KEYS:
                    totals[k] += fd.get(k, 0)

    if days_with_data < MIN_DAYS_FOR_SCORE:
        return (None, days_with_data)

    yield_rate = totals["stories_passed"] / max(totals["stories_fetched"], 1)
    total_art  = (totals["article_ok"]
                  + totals["article_error"]
                  + totals["article_blocked"])
    block_rate = totals["article_blocked"] / max(total_art, 1)

    score = max(0.0, min(1.0, 0.7 * yield_rate - 0.3 * block_rate))
    return (round(score, 4), days_with_data)


# ── Pruning state machine ──────────────────────────────────────────────────────
def run_pruning_transitions(
    feeds_state: list,
    daily_stats: dict,
    today: str = None,
) -> list:
    """
    Evaluate each feed's rolling score and run auto-pruning transitions.

    Transitions (checked daily):
      active   + POOR_DAYS_TO_PROBATION consecutive poor days → probation
      probation + POOR_DAYS_TO_DISABLED consecutive poor days → disabled
      probation|disabled + RECOVERY_DAYS consecutive good days → active

    Mutates feeds_state in place and returns it.
    """
    if today is None:
        today = _today()

    for feed in feeds_state:
        feed_id = feed["id"]
        status  = feed.get("status", "active")
        score, _days = compute_rolling_score(feed_id, daily_stats, today)

        if score is None:
            continue  # not enough data yet

        is_poor = score < POOR_THRESHOLD
        is_good = score >= GOOD_THRESHOLD

        # Good-day streak (drives recovery)
        if is_good:
            feed["good_days_streak"] = feed.get("good_days_streak", 0) + 1
        else:
            feed["good_days_streak"] = 0

        # Recovery: move back to active
        if (status in ("probation", "disabled")
                and feed["good_days_streak"] >= RECOVERY_DAYS):
            feed["status"]                = "active"
            feed["consecutive_poor_days"] = 0
            feed["good_days_streak"]      = 0
            feed["last_transition"]       = today
            print(f"  ✓ Scorecard: {feed['name']} recovered → active  (score={score:.2f})")
            continue

        # Poor-day counter and downward transitions
        if is_poor:
            poor_days = feed.get("consecutive_poor_days", 0) + 1
            feed["consecutive_poor_days"] = poor_days

            if status == "active" and poor_days >= POOR_DAYS_TO_PROBATION:
                feed["status"]          = "probation"
                feed["last_transition"] = today
                print(
                    f"  ⚠  Scorecard: {feed['name']} → probation"
                    f"  (score={score:.2f}, {poor_days} poor days)"
                )
            elif (status == "probation"
                  and poor_days >= POOR_DAYS_TO_PROBATION + POOR_DAYS_TO_DISABLED):
                feed["status"]          = "disabled"
                feed["last_transition"] = today
                print(
                    f"  ✗  Scorecard: {feed['name']} → disabled"
                    f"  (score={score:.2f}, {poor_days} poor days)"
                )
        else:
            feed["consecutive_poor_days"] = 0

    return feeds_state


# ── Post-run ───────────────────────────────────────────────────────────────────
def post_run(rss_feeds: list) -> None:
    """
    Called at the end of each newsletter run.

    1. Aggregates today's telemetry into feed_stats_daily.json.
    2. Runs pruning state-machine transitions.
    3. Saves updated feed state to data/feeds.json.
    """
    today       = _today()
    daily_stats = update_daily_stats(today)
    feeds_state = load_feeds_state()
    feeds_state = run_pruning_transitions(feeds_state, daily_stats, today)
    save_feeds_state(feeds_state)


# ── CLI scorecard ──────────────────────────────────────────────────────────────
def print_scorecard() -> None:
    """Print a formatted scorecard table to stdout."""
    daily_stats = load_daily_stats()
    feeds_state = load_feeds_state()
    today       = _today()

    if not feeds_state:
        print("No feed state found. Run the newsletter at least once to initialise.")
        return

    STATUS_ICON = {"active": "✓", "probation": "⚠", "disabled": "✗"}
    row_fmt = (
        "  {icon} {name:<32}  {status:<10}  {score:>5}  {days:>4}"
        "  {yield_:>5}  {blocked:>6}  {added:>10}"
    )
    header = row_fmt.format(
        icon=" ", name="Feed", status="Status", score="Score",
        days="Days", yield_="Yield", blocked="Block%", added="Added",
    )
    sep = "  " + "-" * (len(header) - 2)

    print(f"\n{'=' * len(header)}")
    print("  FEED SCORECARD  (14-day rolling window)")
    print(f"{'=' * len(header)}")
    print(header)
    print(sep)

    today_dt     = datetime.strptime(today, "%Y-%m-%d")
    window_start = (today_dt - timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")

    for feed in sorted(feeds_state, key=lambda f: f.get("name", "").lower()):
        feed_id = feed["id"]
        status  = feed.get("status", "active")
        icon    = STATUS_ICON.get(status, "?")

        score, days = compute_rolling_score(feed_id, daily_stats, today)

        # Accumulate window totals for display columns
        totals = _blank_day_stats()
        for day, day_data in daily_stats.items():
            if window_start <= day <= today:
                fd = day_data.get(feed_id, {})
                for k in _STAT_KEYS:
                    totals[k] += fd.get(k, 0)

        yield_str = (
            f"{totals['stories_passed'] / max(totals['stories_fetched'], 1):.0%}"
            if totals["stories_fetched"] else "n/a"
        )
        total_art   = (totals["article_ok"]
                       + totals["article_error"]
                       + totals["article_blocked"])
        blocked_str = (
            f"{totals['article_blocked'] / max(total_art, 1):.0%}"
            if total_art else "n/a"
        )
        score_str = f"{score:.2f}" if score is not None else "n/a"

        print(row_fmt.format(
            icon=icon,
            name=feed.get("name", feed_id)[:32],
            status=status,
            score=score_str,
            days=str(days),
            yield_=yield_str,
            blocked=blocked_str,
            added=feed.get("added_at", "?"),
        ))

    print(f"{'=' * len(header)}")
    print(f"  Window: {window_start} → {today}\n")


# ── CLI entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print_scorecard()
