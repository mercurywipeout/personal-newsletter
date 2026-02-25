# AI & Tech Signal

A daily newsletter that fetches AI and tech news from RSS feeds, curates the best stories using Claude, and delivers a formatted HTML email every morning.

## How it works

1. Pulls stories from 8 RSS feeds (TechCrunch, The Verge, Ars Technica, VentureBeat, MIT Tech Review, Hacker News, The Information, Import AI)
2. Sends the raw stories to Claude, which selects 6–8 high-signal items and rewrites them with concise summaries
3. Builds a responsive HTML email grouped by category
4. Sends it via Gmail SMTP

Runs automatically every day at **7 AM PT** via GitHub Actions.

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### 2. Add GitHub Secrets

In your repo go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GMAIL_USER` | Gmail address to send from |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://myaccount.google.com/apppasswords) (not your regular password) |
| `RECIPIENT_EMAIL` | Email address to deliver the newsletter to |

### 3. Enable GitHub Actions

The workflow file at `.github/workflows/daily-newsletter.yml` will run automatically once the secrets are set. You can also trigger it manually from the **Actions** tab.

## Running locally

```bash
pip install -r requirements.txt
python daily_newsletter.py
```

Create a `newsletter.config` file in the project root with your credentials:

```
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
RECIPIENT_EMAIL=you@gmail.com
ANTHROPIC_API_KEY=sk-ant-...
```

This file is gitignored and will never be committed.

## Story categories

Claude organizes stories into three categories:

- **AI Models & Research** — new model releases, papers, benchmark results
- **Developer Tools & Infrastructure** — APIs, frameworks, open-source projects
- **Big Tech & Industry Moves** — strategy shifts, launches, acquisitions, funding

Each story gets a signal tag: 🔴 Major news · 🟡 Worth watching · 🟢 Early signal

## Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account with an App Password enabled
