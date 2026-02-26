#!/usr/bin/env python3
"""
Daily AI & Tech Signal Newsletter
Fetches top stories from RSS feeds, curates them with Claude, and sends a formatted email.
"""

import os
import re
import json
import time
import smtplib
import feedparser
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

# ── RSS Sources ────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "Import AI", "url": "https://importai.substack.com/feed"},
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml"},
    {"name": "Anthropic News", "url": "https://www.anthropic.com/news/rss.xml"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "Hacker News Frontpage", "url": "https://hnrss.org/frontpage"},
    {"name": "Reddit r/MachineLearning", "url": "https://www.reddit.com/r/MachineLearning/.rss"},
    {"name": "Reddit r/LocalLLaMA", "url": "https://www.reddit.com/r/LocalLLaMA/.rss"},
    {"name": "Ars Technica AI", "url": "https://arstechnica.com/ai/feed"},
    {"name": "One Useful Thing", "url": "https://oneusefulthing.substack.com/feed"},
    {"name": "Nathan Lambert Substack", "url": "https://natolambert.substack.com/feed"},
    {"name": "Google DeepMind News", "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "The GitHub Blog", "url": "https://github.blog/feed"}  
]

# ── Fetch Stories ──────────────────────────────────────────────────────────────
def fetch_recent_stories():
    stories = []
    cutoff = datetime.now() - timedelta(hours=36)  # slightly wider net

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:25]:
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                    except Exception:
                        pass

                if published is None or published > cutoff:
                    raw_summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = re.sub(r"<[^>]+>", " ", raw_summary)
                    clean_summary = re.sub(r"\s+", " ", clean_summary).strip()[:600]

                    stories.append({
                        "source":    feed_info["name"],
                        "title":     entry.get("title", "").strip(),
                        "summary":   clean_summary,
                        "url":       entry.get("link", ""),
                        "published": published.strftime("%Y-%m-%d %H:%M") if published else "recent",
                    })
        except Exception as e:
            print(f"  ⚠  Could not fetch {feed_info['name']}: {e}")

    print(f"  Fetched {len(stories)} raw stories across {len(RSS_FEEDS)} feeds")
    return stories


# ── Claude Curation ────────────────────────────────────────────────────────────
def curate_with_claude(stories):
    import anthropic
    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])

    today_str = datetime.now().strftime("%B %d, %Y")
    stories_json = json.dumps(stories, indent=2)

    prompt = f"""You are the editor of a sharp daily newsletter called "AI & Tech Signal." Your reader is a tech-savvy professional who wants early-signal intelligence — not just mainstream news, but emerging trends and things that haven't reached the mainstream radar yet.

Today's date: {today_str}

Here are raw stories pulled from RSS feeds in the last 36 hours:

{stories_json}

Your job:
1. Select the 6–8 most valuable stories across these three categories:
   • **AI Models & Research** — new model releases, breakthrough papers, benchmark results
   • **Developer Tools & Infrastructure** — APIs, frameworks, open-source projects gaining traction
   • **Big Tech & Industry Moves** — strategy shifts, product launches, acquisitions, funding rounds

2. Prioritize EARLY SIGNAL: things gaining momentum before they're mainstream. Deprioritize press releases and fluff.

3. For each selected story write:
   - A punchy, specific headline (rewrite if the original is vague or clickbait-y)
   - A 2–3 sentence summary focused on WHY it matters and what to watch for
   - A signal tag: one of "🔴 Major news", "🟡 Worth watching", or "🟢 Early signal"
   - The category: "AI Models & Research", "Developer Tools & Infra", or "Big Tech & Industry"

Return ONLY a valid JSON array. No markdown, no explanation, no code fences.

[
  {{
    "title": "...",
    "summary": "...",
    "signal": "🟡 Worth watching",
    "category": "Developer Tools & Infra",
    "source": "...",
    "url": "..."
  }}
]"""

    message = client.messages.create(
        model="claude-opus-4-5-20251101",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        return json.loads(match.group())

    print("  ⚠  Could not parse Claude response — using raw stories as fallback")
    return []


# ── Build HTML Email ───────────────────────────────────────────────────────────
def build_html_email(stories):
    today_long  = datetime.now().strftime("%A, %B %d, %Y")
    today_short = datetime.now().strftime("%b %d")

    # Group by category
    categories = {}
    for s in stories:
        cat = s.get("category", "General")
        categories.setdefault(cat, []).append(s)

    sections_html = ""
    for cat, items in categories.items():
        story_blocks = ""
        for s in items:
            story_blocks += f"""
            <div style="margin-bottom:24px;padding-bottom:24px;border-bottom:1px solid #f0eeeb;">
              <div style="font-size:11px;color:#999;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">
                {s.get('signal','')}&nbsp;&nbsp;·&nbsp;&nbsp;{s.get('source','')}
              </div>
              <a href="{s.get('url','#')}" style="text-decoration:none;">
                <h3 style="margin:0 0 8px 0;font-size:17px;font-weight:600;line-height:1.35;color:#1a1a1a;">
                  {s.get('title','')}
                </h3>
              </a>
              <p style="margin:0;font-size:14px;line-height:1.65;color:#444;">
                {s.get('summary','')}
              </p>
              <a href="{s.get('url','#')}" style="display:inline-block;margin-top:8px;font-size:12px;color:#0057ff;text-decoration:none;font-weight:500;">
                Read more →
              </a>
            </div>"""

        sections_html += f"""
        <div style="margin-bottom:36px;">
          <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:#0057ff;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #0057ff;">
            {cat}
          </div>
          {story_blocks}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI &amp; Tech Signal — {today_short}</title>
</head>
<body style="margin:0;padding:0;background:#f2f1ee;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;">

  <div style="max-width:620px;margin:32px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:#0a0a0a;padding:36px 44px 32px;">
      <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:2.5px;margin-bottom:10px;">Daily Intelligence</div>
      <div style="font-size:30px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;line-height:1;">AI &amp; Tech Signal</div>
      <div style="margin-top:10px;font-size:13px;color:#888;">{today_long}</div>
    </div>

    <!-- Intro -->
    <div style="padding:28px 44px 0;border-bottom:1px solid #f0eeeb;">
      <p style="margin:0 0 24px;font-size:14px;line-height:1.7;color:#555;">
        Your daily curation of what's moving in AI, developer tools, and big tech — filtered for signal over noise.
      </p>
    </div>

    <!-- Stories -->
    <div style="padding:32px 44px;">
      {sections_html}
    </div>

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
def send_email(html_content):
    today_short = datetime.now().strftime("%b %d")
    subject = f"AI & Tech Signal — {today_short}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"AI & Tech Signal <{CONFIG['GMAIL_USER']}>"
    msg["To"]      = CONFIG["RECIPIENT_EMAIL"]
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(CONFIG["GMAIL_USER"], CONFIG["GMAIL_APP_PASSWORD"])
        server.sendmail(CONFIG["GMAIL_USER"], CONFIG["RECIPIENT_EMAIL"], msg.as_string())

    print(f"  ✓ Email sent → {CONFIG['RECIPIENT_EMAIL']}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  AI & Tech Signal  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    print("\n[1/4] Fetching stories from RSS feeds...")
    stories = fetch_recent_stories()

    if not stories:
        print("  No stories found — aborting.")
        return

    print("\n[2/4] Curating with Claude...")
    curated = curate_with_claude(stories)
    print(f"  Selected {len(curated)} stories for the newsletter")

    if not curated:
        print("  Curation failed — aborting.")
        return

    print("\n[3/4] Building HTML email...")
    html = build_html_email(curated)

    print("\n[4/4] Sending email...")
    send_email(html)

    print(f"\n  Done! Newsletter delivered.\n")


if __name__ == "__main__":
    main()
