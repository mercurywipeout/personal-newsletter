# Daily Personal Newsletter

A daily AI & tech newsletter that fetches stories from RSS feeds, deduplicates and clusters them, curates the best with Claude, and delivers a formatted HTML email every morning.

## How it works

1. **Fetch** — pulls stories from a configurable list of RSS feeds (last 36 hours)
2. **Enrich** — fetches full article text for better deduplication signal
3. **Cluster** — groups near-duplicate stories using SimHash; one representative per event
4. **Curate** — sends representatives to Claude, which selects 6–8 high-signal items with concise summaries, signal tags, and a situational briefing
5. **Deliver** — builds a responsive HTML email and sends it via Gmail SMTP

Runs automatically every day at **7 AM PT** via GitHub Actions.

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
pip install -r requirements.txt
```

### 2. Add GitHub Secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GMAIL_USER` | Gmail address to send from |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `RECIPIENT_EMAIL` | Delivery address |

### 3. Enable GitHub Actions

`.github/workflows/daily-newsletter.yml` runs automatically once secrets are set.
Trigger manually from the **Actions** tab at any time.

### 4. Clear runtime state (if you forked)

This repo commits runtime state (`seen_stories.json`, `data/feeds.json`, `data/feed_stats_daily.json`, `data/runs/`) back to main after each run. If you forked it, delete these files before your first run so you start fresh:

```bash
rm -f seen_stories.json data/feeds.json data/feed_stats_daily.json
rm -rf data/runs/
git commit -am "chore: clear runtime state" && git push
```

---

## Running locally

```bash
python daily_newsletter.py          # full run (sends email)
python daily_newsletter.py --dry-run  # preview only, saves newsletter_preview.html
```

Create a `newsletter.config` file in the project root:

```
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
RECIPIENT_EMAIL=you@gmail.com
ANTHROPIC_API_KEY=sk-ant-...
```

This file is gitignored and never committed.

---

## Feed management

Feeds are defined in `config/feeds.seed.json`. Each entry has a name, URL, category, and priority. On first run, this seed is bootstrapped into `data/feeds.json`, which tracks runtime state (status, performance streaks).

The scorecard system automatically moves low-performing feeds to `probation` or `disabled` status based on rolling click and pass-through rates. It recovers feeds that improve.

### Adding feeds

Edit `config/feeds.seed.json`. The runtime state file (`data/feeds.json`) is committed by CI and re-created from the seed if absent.

### Weekly feed discovery

A separate workflow (`.github/workflows/weekly-feed-discovery.yml`) runs every Sunday. It analyzes outbound links from recent articles, discovers candidate RSS/Atom feeds, validates them, and writes ranked recommendations to `data/feed_recommendations.json`.

Run it manually:

```bash
# Discover feeds from a specific cluster date
python -m discovery.recommend_feeds --day 2026-03-01

# Also auto-add top candidates as probation feeds
python -m discovery.recommend_feeds --day 2026-03-01 --auto-add
```

---

## Story categories

Stories are organized into three categories:

- **AI Models & Research** — model releases, papers, benchmark results
- **Developer Tools & Infrastructure** — APIs, frameworks, open-source projects
- **Big Tech & Industry Moves** — strategy shifts, launches, acquisitions, funding

Each story gets a signal tag: 🔴 Major news · 🟡 Worth watching · 🟢 Early signal

---

## Project structure

```
daily_newsletter.py          # main entry point
config/
  feeds.seed.json            # committed feed definitions (seed)
article_fetcher.py           # full-text enrichment (urllib3 + trafilatura)
clustering/                  # SimHash deduplication
scoring/                     # feed scorecard and telemetry
discovery/                   # weekly feed recommendation system
data/                        # runtime state (committed by CI)
.github/workflows/
  daily-newsletter.yml       # daily send at 7 AM PT
  weekly-feed-discovery.yml  # Sunday feed discovery
```

---

## Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account with an App Password enabled

---

## Roadmap / TODO

- [ ] **Add scraping support** — some high-value sources (e.g. `claude.com/blog`) don't publish RSS feeds; add a scraper that can fetch a listing page, parse article URLs, and pass them through `article_fetcher.py` like any RSS-sourced story
