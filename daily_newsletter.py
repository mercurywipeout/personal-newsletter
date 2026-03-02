#!/usr/bin/env python3
"""
Daily Systems Brief Newsletter
Fetches top stories from RSS feeds, curates them with Claude, and sends a formatted email.
"""

import os
import re
import sys
import json
import time
import socket
import smtplib
import feedparser
import concurrent.futures
from article_fetcher import enrich_stories
from clustering.cluster import cluster_stories, save_clusters, emit_cluster_telemetry
from scoring.scorecard import sync_feeds_state, post_run, get_rate_limit, slug, emit_telemetry
from html import escape
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Load config ────────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "newsletter.config"

def load_config():
    config = {}
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                config[key.strip()] = val.strip()
    # Env vars override config file
    for key in ["GMAIL_USER", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL", "ANTHROPIC_API_KEY"]:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config

CONFIG = load_config()

_REQUIRED_KEYS = ["GMAIL_USER", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL", "ANTHROPIC_API_KEY"]

def validate_config():
    missing = [k for k in _REQUIRED_KEYS if not CONFIG.get(k)]
    if missing:
        for k in missing:
            print(f"  ✗ Missing config: {k} — set it in newsletter.config or as an environment variable")
        raise SystemExit(1)

# ── Deduplication ──────────────────────────────────────────────────────────────
SEEN_PATH    = Path(__file__).parent / "seen_stories.json"
SEEN_TTL_DAYS = 7

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "ref", "referrer", "source", "fbclid", "gclid", "msclkid", "twclid", "_ga", "_gl",
}

def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_qs = [(k, v) for k, v in parse_qsl(parsed.query)
                    if k.lower() not in _TRACKING_PARAMS]
        return urlunparse(parsed._replace(query=urlencode(clean_qs), fragment=""))
    except Exception:
        return url

def load_seen_urls():
    if not SEEN_PATH.exists():
        return set()
    try:
        cutoff = datetime.now() - timedelta(days=SEEN_TTL_DAYS)
        data = json.loads(SEEN_PATH.read_text())
        return {e["url"] for e in data if datetime.fromisoformat(e["date"]) > cutoff}
    except Exception:
        return set()

def save_seen_urls(new_urls):
    today = datetime.now().date().isoformat()
    cutoff = datetime.now() - timedelta(days=SEEN_TTL_DAYS)
    existing = []
    if SEEN_PATH.exists():
        try:
            existing = [e for e in json.loads(SEEN_PATH.read_text())
                        if datetime.fromisoformat(e["date"]) > cutoff]
        except Exception:
            pass
    existing_urls = {e["url"] for e in existing}
    for url in new_urls:
        if url not in existing_urls:
            existing.append({"url": url, "date": today})
    SEEN_PATH.write_text(json.dumps(existing, indent=2))

# ── RSS Sources ────────────────────────────────────────────────────────────────
FEEDS_PATH = Path(__file__).parent / "feeds.json"

def load_feeds():
    feeds = json.loads(FEEDS_PATH.read_text())
    result = [f for f in feeds if f.get("enabled", True)]
    for f in result:
        f.setdefault("id", slug(f["name"]))
    return result

RSS_FEEDS = load_feeds()

# ── Fetch Stories ──────────────────────────────────────────────────────────────
def _fetch_one_feed(feed_info, seen_urls, cutoff):
    stories = []
    try:
        feed = feedparser.parse(feed_info["url"])
        for i, entry in enumerate(feed.entries[:25]):
            published = None
            for date_field in ("published_parsed", "updated_parsed"):
                raw = getattr(entry, date_field, None)
                if raw:
                    try:
                        published = datetime.fromtimestamp(time.mktime(raw))
                        break
                    except Exception:
                        pass

            # No date: include only the top 5 entries (feeds are reverse-chronological)
            if published is None and i >= 5:
                continue

            if published is None or published > cutoff:
                raw_url = entry.get("link", "")
                if normalize_url(raw_url) in seen_urls:
                    continue

                raw_summary = entry.get("summary", entry.get("description", ""))
                clean_summary = re.sub(r"<[^>]+>", " ", raw_summary)
                clean_summary = re.sub(r"\s+", " ", clean_summary).strip()[:600]

                stories.append({
                    "source":    feed_info["name"],
                    "feed_id":   feed_info.get("id", ""),
                    "title":     entry.get("title", "").strip(),
                    "summary":   clean_summary,
                    "url":       raw_url,
                    "published": published.strftime("%Y-%m-%d %H:%M") if published else "recent",
                })
    except Exception as e:
        print(f"  ⚠  Could not fetch {feed_info['name']}: {e}")
    return stories


def fetch_recent_stories(seen_urls=None):
    if seen_urls is None:
        seen_urls = set()
    cutoff = datetime.now() - timedelta(hours=36)

    all_stories = []
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(10)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_fetch_one_feed, fi, seen_urls, cutoff): fi for fi in RSS_FEEDS}
            try:
                for future in concurrent.futures.as_completed(futures, timeout=60):
                    try:
                        all_stories.extend(future.result())
                    except Exception as e:
                        print(f"  ⚠  Feed fetch error: {e}")
            except concurrent.futures.TimeoutError:
                print("  ⚠  Feed fetching timed out after 60s — using partial results")
    finally:
        socket.setdefaulttimeout(old_timeout)

    # Dedup by normalized URL across all feeds
    seen_this_run = set()
    stories = []
    for s in all_stories:
        key = normalize_url(s["url"])
        if key not in seen_this_run:
            seen_this_run.add(key)
            stories.append(s)

    # Emit story_fetched telemetry (sequential — after thread pool completes)
    for s in stories:
        if s.get("feed_id"):
            emit_telemetry({"event": "story_fetched", "feed_id": s["feed_id"]})

    print(f"  Fetched {len(stories)} raw stories across {len(RSS_FEEDS)} feeds")
    return stories


# ── Pre-filter ─────────────────────────────────────────────────────────────────
def pre_filter_stories(stories, per_source=8, total_cap=100, feeds_state_by_id=None):
    # Drop entries missing title, url, or a meaningful summary
    stories = [s for s in stories
               if s.get("title") and s.get("url") and len(s.get("summary", "")) >= 30]

    # Undated entries sort just above the cutoff — possibly recent, but below confirmed timestamps
    _undated_rank = datetime.now() - timedelta(hours=34)

    def recency_key(s):
        p = s.get("published", "")
        if not p or p == "recent":
            return _undated_rank
        try:
            return datetime.strptime(p, "%Y-%m-%d %H:%M")
        except Exception:
            return datetime.min

    # Take the most recent N stories per feed, where N comes from the scorecard
    by_source = {}
    for s in stories:
        by_source.setdefault(s["source"], []).append(s)

    filtered = []
    for source_stories in by_source.values():
        feed_id = source_stories[0].get("feed_id", "") if source_stories else ""
        if feeds_state_by_id and feed_id:
            status = feeds_state_by_id.get(feed_id, {}).get("status", "active")
            limit  = get_rate_limit(status)
        else:
            limit = per_source
        if limit == 0:
            continue  # disabled feed — drop entirely
        source_stories.sort(key=recency_key, reverse=True)
        filtered.extend(source_stories[:limit])

    # Emit story_passed telemetry
    for s in filtered:
        if s.get("feed_id"):
            emit_telemetry({"event": "story_passed", "feed_id": s["feed_id"]})

    # Final sort by recency and hard cap
    filtered.sort(key=recency_key, reverse=True)
    return filtered[:total_cap]


# ── Claude Curation ────────────────────────────────────────────────────────────
def curate_with_claude(stories):
    import anthropic
    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])

    today_str = datetime.now().strftime("%B %d, %Y")
    stories_json = json.dumps(stories, indent=2)

    prompt = f"""You are the editor of a sharp daily newsletter called "Systems Brief."

Your reader is a tech-savvy professional who wants high-signal awareness of what materially moved in AI and tech in the last 36 hours. They do NOT want forced narratives, hype, or artificial thematic cohesion.

Today's date: {today_str}

Here are raw stories pulled from RSS feeds in the last 36 hours:

{stories_json}

Your job:

0. First, analyze the full set of stories and identify what materially changed in the AI and tech landscape in the last 36 hours.

Write a 2–3 sentence situational briefing that:
- Summarizes what moved without forcing a single overarching narrative
- Avoids hype or sweeping claims
- Distinguishes confirmed developments from speculation
- Does NOT repeat individual story summaries
- Clearly states if signals are fragmented or incremental

0.25. Write the following synthesis sections (no emojis anywhere in these sections):

Emerging patterns (2–3 bullets): What structural or directional patterns are emerging across multiple stories? Focus on second-order effects and cross-cutting trends, not individual events. Each bullet is a concise sentence.

Quiet signals (1–3 bullets): Low-visibility developments that deserve attention but didn't make the main story selection. Think: early adoption patterns, under-covered releases, or subtle behavioral shifts. Each bullet is a concise sentence.

0.5. Based on your analysis, generate a concise subject line.

Subject line rules:
- Reflect the most significant confirmed development in the last 36 hours
- If no single dominant shift exists, reflect the strongest cluster of meaningful signals
- Use concrete nouns and specific developments (avoid abstractions)
- Avoid hype, drama, urgency language, or emotional framing
- Do NOT use clickbait structures
- Max 12 words
- Clear > clever
- No emojis
- No quotation marks

The subject line should read like a compressed log entry of what changed.

1. Select the 6–8 most valuable stories across these three categories:
   • AI Models & Research — new model releases, breakthrough papers, benchmark results
   • Developer Tools & Infrastructure — APIs, frameworks, open-source projects gaining traction
   • Big Tech & Industry Moves — strategy shifts, product launches, acquisitions, funding rounds

2. Prioritize EARLY SIGNAL: meaningful developments before they are fully mainstream.
   Prefer:
   - Architecture-level changes over minor features
   - Infrastructure shifts over incremental upgrades
   - Behavioral or adoption shifts over marketing announcements
   - Credible evidence over hype

3. For each selected story write:
   - A punchy, specific headline (rewrite vague or clickbait titles)
   - A 2–3 sentence summary focused on WHY it matters and what to watch for
   - Measured language — avoid overstating structural impact unless clearly justified
   - A signal tag: one of "Major news", "Worth watching", or "Early signal" (no emojis)
   - The category: "AI Models & Research", "Developer Tools & Infra", or "Big Tech & Industry"
   - The original source name and URL

Return ONLY a valid JSON object with this structure:

{{
  "subject_line": "...",
  "situational_briefing": "2–3 sentence overview here",
  "emerging_patterns": ["pattern sentence 1", "pattern sentence 2"],
  "quiet_signals": ["signal sentence 1", "signal sentence 2"],
  "stories": [
    {{
      "title": "...",
      "summary": "...",
      "signal": "Worth watching",
      "category": "Developer Tools & Infra",
      "source": "...",
      "url": "..."
    }}
  ]
}}
"""

    message = client.messages.create(
        model="claude-opus-4-5-20251101",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # Try envelope format: {"subject_line": "...", "situational_briefing": "...", "stories": [...], ...}
    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if obj_match:
        try:
            parsed = json.loads(obj_match.group())
            if "stories" in parsed:
                return (
                    parsed.get("subject_line", ""),
                    parsed.get("situational_briefing", ""),
                    parsed.get("emerging_patterns", []),
                    parsed.get("quiet_signals", []),
                    parsed["stories"],
                )
        except Exception:
            pass

    # Fallback: bare array
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if arr_match:
        return "", "", [], [], json.loads(arr_match.group())

    print("  ⚠  Could not parse Claude response — using raw stories as fallback")
    return "", "", [], [], []


def safe_url(url):
    url = str(url).strip()
    if url.startswith(("http://", "https://")) and '"' not in url and "'" not in url:
        return url
    return "#"


# ── Build HTML Email ───────────────────────────────────────────────────────────
def build_html_email(stories, situational_briefing="", emerging_patterns=None, quiet_signals=None):
    today_long  = datetime.now().strftime("%A, %B %d, %Y")
    today_short = datetime.now().strftime("%b %d")

    # ── Emerging Patterns (subsection below briefing) ───────────────────────────
    emerging_html = ""
    if emerging_patterns:
        bullets = "".join(
            f'<li style="font-size:13px;line-height:1.65;color:#555;margin-bottom:5px;">{escape(b)}</li>'
            for b in emerging_patterns
        )
        emerging_html = f"""
      <div style="margin-top:16px;margin-bottom:4px;">
        <div style="font-size:10px;font-weight:600;color:#1a1a1a;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
          Emerging patterns
        </div>
        <ul style="margin:0;padding:0 0 0 16px;">{bullets}</ul>
      </div>"""

    # ── Quiet Signals (bottom of email, before footer) ──────────────────────────
    quiet_signals_html = ""
    if quiet_signals:
        bullets = "".join(
            f'<li style="font-size:13px;line-height:1.65;color:#666;margin-bottom:5px;">{escape(b)}</li>'
            for b in quiet_signals
        )
        quiet_signals_html = f"""
  <div style="padding:20px 44px 24px;border-top:1px solid #ece9e3;">
    <div style="font-size:10px;font-weight:600;color:#1a1a1a;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">
      Quiet signals
    </div>
    <ul style="margin:0;padding:0 0 0 16px;">{bullets}</ul>
  </div>"""

    # ── Story sections grouped by category ─────────────────────────────────────
    categories = {}
    for s in stories:
        cat = s.get("category", "General")
        categories.setdefault(cat, []).append(s)

    sections_html = ""
    for cat, items in categories.items():
        story_blocks = ""
        for s in items:
            url = safe_url(s.get('url', ''))
            story_blocks += f"""
            <div style="margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #f0eeeb;">
              <div style="font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">
                {escape(s.get('signal',''))}&nbsp;&nbsp;·&nbsp;&nbsp;{escape(s.get('source',''))}
              </div>
              <a href="{url}" style="text-decoration:none;">
                <h3 style="margin:0 0 8px 0;font-size:17px;font-weight:600;line-height:1.35;color:#1a1a1a;">
                  {escape(s.get('title',''))}
                </h3>
              </a>
              <p style="margin:0;font-size:14px;line-height:1.65;color:#444;">
                {escape(s.get('summary',''))}
              </p>
              <a href="{url}" style="display:inline-block;margin-top:8px;font-size:12px;color:#0057ff;text-decoration:none;font-weight:500;">
                Read more →
              </a>
            </div>"""

        sections_html += f"""
        <div style="margin-bottom:36px;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#0057ff;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #0057ff;">
            {escape(cat)}
          </div>
          {story_blocks}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Systems Brief — {today_short}</title>
</head>
<body style="margin:0;padding:0;background:#f2f1ee;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;">

  <div style="max-width:620px;margin:32px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:#0a0a0a;padding:36px 44px 32px;">
      <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:2.5px;margin-bottom:10px;">Daily Intelligence</div>
      <div style="font-size:30px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;line-height:1;">Systems Brief</div>
      <div style="margin-top:10px;font-size:13px;color:#888;">{today_long}</div>
    </div>

    <!-- Intro / Situational Briefing + Emerging Patterns -->
    <div style="padding:28px 44px 0;border-bottom:1px solid #f0eeeb;">
      <p style="margin:0 0 {('26' if emerging_patterns else '34')}px;font-size:14px;line-height:1.7;color:#555;">
        {escape(situational_briefing) if situational_briefing else "Your daily curation of what&#39;s moving in AI, developer tools, and big tech — filtered for signal over noise."}
      </p>
      {emerging_html}
      <div style="height:24px;"></div>
    </div>

    <!-- Stories -->
    <div style="padding:32px 44px;">
      {sections_html}
    </div>

    <!-- Quiet Signals -->
    {quiet_signals_html}

    <!-- Footer -->
    <div style="background:#f8f7f4;padding:24px 44px;border-top:1px solid #ece9e3;">
      <p style="margin:0;font-size:11px;color:#aaa;line-height:1.7;">
        Curated by Claude · Sources: {", ".join(f["name"] for f in RSS_FEEDS)}
        <br>You're receiving this because you set it up. To stop, disable actions in the GitHub repo's settings.
      </p>
    </div>

  </div>
</body>
</html>"""


# ── Send Email ─────────────────────────────────────────────────────────────────
def send_email(html_content, subject_line=""):
    today_short = datetime.now().strftime("%b %d")
    subject = subject_line if subject_line else f"Systems Brief — {today_short}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Systems Brief <{CONFIG['GMAIL_USER']}>"
    msg["To"]      = CONFIG["RECIPIENT_EMAIL"]
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(CONFIG["GMAIL_USER"], CONFIG["GMAIL_APP_PASSWORD"])
        server.sendmail(CONFIG["GMAIL_USER"], CONFIG["RECIPIENT_EMAIL"], msg.as_string())

    print(f"  ✓ Email sent → {CONFIG['RECIPIENT_EMAIL']}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv

    print(f"\n{'='*55}")
    print(f"  Systems Brief  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if dry_run:
        print("  DRY RUN — no email will be sent, seen_stories not updated")
    print(f"{'='*55}")

    validate_config()

    feeds_state       = sync_feeds_state(RSS_FEEDS)
    feeds_state_by_id = {f["id"]: f for f in feeds_state}

    seen_urls = load_seen_urls()
    print(f"  Loaded {len(seen_urls)} previously seen URLs (last {SEEN_TTL_DAYS} days)")

    print("\n[1/4] Fetching stories from RSS feeds...")
    stories = fetch_recent_stories(seen_urls)

    if not stories:
        print("  No stories found — aborting.")
        return

    stories = pre_filter_stories(stories, feeds_state_by_id=feeds_state_by_id)
    print(f"  Pre-filtered to {len(stories)} stories (≤8 per source, ≤100 total)")
    enrich_stories(stories)

    all_stories = stories  # keep full list for cluster telemetry
    stories, clusters = cluster_stories(stories)
    save_clusters(clusters)
    emit_cluster_telemetry(all_stories, stories, clusters)
    n_dupes = len(all_stories) - len(stories)
    print(f"  Clustered: {n_dupes} duplicates removed → {len(stories)} unique stories")

    print("\n[2/4] Curating with Claude...")
    subject_line, briefing, emerging_patterns, quiet_signals, curated = curate_with_claude(stories)
    print(f"  Selected {len(curated)} stories for the newsletter")

    if not curated:
        print("  Curation failed — aborting.")
        return

    print("\n[3/4] Building HTML email...")
    html = build_html_email(curated, briefing, emerging_patterns, quiet_signals)

    if dry_run:
        preview_path = Path(__file__).parent / "newsletter_preview.html"
        preview_path.write_text(html)
        print(f"\n  Subject: {subject_line or '(none returned by Claude)'}")
        print(f"  Dry run complete. Preview saved to {preview_path.name}")
        print("  Open it in a browser to inspect the output.\n")
        post_run(RSS_FEEDS)
        return

    print("\n[4/4] Sending email...")
    send_email(html, subject_line)

    new_seen = {normalize_url(s["url"]) for s in curated if s.get("url")}
    save_seen_urls(new_seen)
    print(f"  Saved {len(new_seen)} URLs to seen history")

    post_run(RSS_FEEDS)
    print(f"\n  Done! Newsletter delivered.\n")


if __name__ == "__main__":
    main()
