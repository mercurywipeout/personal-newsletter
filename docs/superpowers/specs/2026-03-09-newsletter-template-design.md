# Newsletter Template — Design Spec
**Date:** 2026-03-09
**Branch:** `template`
**Goal:** Generalize the AI/tech newsletter into a forkable, self-managing newsletter engine anyone can point at any topic.

---

## Problem

The current codebase is a fully-functional, self-managing newsletter system — but it is hardcoded to an AI & tech newsletter. Topic-specific language is embedded in the Claude curation prompt, the feed discovery relevance scorer, the email template, section titles, and documentation. A forker would need to hunt down and replace these references manually with no clear guidance on what to change.

## Solution

Introduce a single `config/newsletter.yaml` config file that captures the newsletter's identity (name, brief, tone). Thread these values through the pipeline so every component adapts automatically. Replace all hardcoded topic language with template variables. Remove the AI-specific `notable_uses` feature. The result: fork, fill in the config, add GitHub Secrets, done.

---

## Configuration

### `config/newsletter.yaml` (new, committed)
The primary onboarding artifact. Every forker edits exactly this file.

```yaml
name: "Your Newsletter Name"
brief: |
  Describe your newsletter's topic, audience, and editorial focus here.
  Claude uses this to shape the curation, tone, and situational briefing.
  2–4 sentences is ideal.
tone: executive  # options: executive | analytical | conversational | accessible
```

**Tone options:**
- `executive` — concise, decision-focused, "what this means for you"
- `analytical` — data-driven, nuanced, surfaces uncertainty and counterarguments
- `conversational` — friendly, opinionated, like a smart colleague's message
- `accessible` — plain language, explains jargon, suited for mixed-expertise audiences

Free-text tone is also valid (e.g. `tone: "dry and sardonic"`).

### `newsletter.config` (gitignored, local only)
Secrets for local development. Production uses GitHub Secrets.

```
GMAIL_USER=...
GMAIL_APP_PASSWORD=...
RECIPIENT_EMAIL=...
ANTHROPIC_API_KEY=...
```

### `config/feeds.seed.json`
Unchanged in format. Ships with 3–4 neutral example feeds covering a generic topic. Forkers replace these with their own feeds.

### Schedule
Defined in `.github/workflows/daily-newsletter.yml` (GitHub Actions cron). A prominent comment at the top of the workflow file explains how to change it. Not in `newsletter.yaml` — schedule is a GitHub Actions concern, not an app concern.

---

## Claude Curation Prompt

The `curate_with_claude()` function is rewritten to be fully driven by `newsletter.yaml` values.

**Prompt inputs:** `name`, `brief`, `tone`, candidate stories.

**What changes:**
- All AI/tech-specific framing removed
- Categories are **not** prescribed — Claude infers 2–4 category names from the day's stories and returns them as part of the response
- Tone shapes the briefing style, summary register, and subject line voice
- Signal tags (`Major news`, `Worth watching`, `Early signal`) are unchanged — they are topic-agnostic

**Return value change:** Claude now returns dynamic category names alongside the existing 5-tuple fields. `build_html_email()` accepts these instead of the hardcoded category list.

**Sections preserved:** subject line, situational briefing, emerging patterns, quiet signals, curated stories (6–8).

---

## Feed Discovery — Topical Relevance

`discovery/recommend_feeds.py` currently uses a hardcoded `_TECH_KEYWORDS` set to score candidate feeds as relevant or off-topic. This is the most topic-specific piece in the codebase.

**Change:** Replace keyword scoring with a Claude call during the weekly discovery run.

- Inputs to Claude: newsletter `brief` + candidate feed's title, description, and sample entry titles
- Claude returns a relevance score (0–10) and a short reason
- Score replaces the `+3 / -4` keyword heuristic in `score_candidate()`
- Off-topic domain persistence (`data/rejected_domains.json`) stays as-is — domain blocklisting is topic-agnostic

This keeps feed discovery self-managing across any topic without requiring the forker to maintain keyword lists.

---

## Module Changes

| Module | Change |
|---|---|
| `daily_newsletter.py` | Load `newsletter.yaml` at startup; pass `name`/`brief`/`tone` into `curate_with_claude()` and `build_html_email()`; accept dynamic categories from Claude |
| `discovery/recommend_feeds.py` | Replace `_TECH_KEYWORDS` heuristic with Claude-powered relevance scoring |
| `config/feeds.seed.json` | Replace AI/tech feeds with neutral example feeds |
| `clustering/cluster.py` | **Unchanged** — topic-agnostic |
| `scoring/scorecard.py` | **Unchanged** — topic-agnostic |
| `article_fetcher.py` | **Unchanged** — topic-agnostic |

---

## Removed from Template

- `notable_uses.py` — AI-specific feature, not part of the general template
- `test_notable_uses.py` — removed with the above
- All hardcoded AI/tech category strings (`AI Models & Research`, `Developer Tools & Infrastructure`, `Big Tech & Industry Moves`)
- `notable_uses` parameter and HTML block in `build_html_email()`
- "Noted: AI in Practice" section title

---

## New Files

- `config/newsletter.yaml` — user config (committed, not gitignored)
- `config/newsletter.yaml.example` — filled-in example showing a real newsletter config

---

## Repository / Docs Changes

- `README.md` — rewritten as topic-agnostic "5-minute setup" guide
- `CLAUDE.md` — updated to describe the general system
- `.github/workflows/daily-newsletter.yml` — prominent schedule comment added at top
- `docs/superpowers/specs/` — this spec

---

## Branch Strategy

Work on a `template` branch in this repo. When the template is stable and tested with `--dry-run`, extract to a new GitHub template repository (fresh `git init`, clean history).

---

## Success Criteria

A person with no prior knowledge of this codebase can:
1. Fork the template repo
2. Edit `config/newsletter.yaml` (name + brief + tone)
3. Edit `config/feeds.seed.json` (add their feeds)
4. Add 4 GitHub Secrets
5. Trigger a manual run from the Actions tab
6. Receive a correctly themed, on-topic newsletter email

No other files need to be edited.
