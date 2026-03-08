#!/usr/bin/env python3
"""
Notable AI Uses — personal signal layer.

Surfaces real-world, clever, non-obvious uses of AI being shared online:
workflows, experiments, hacks, agent setups, automation scripts.

Two-stage pipeline:
  1. score_ai_use_candidate(story)  → float [pure, no I/O]
  2. find_notable_ai_uses(stories, api_key) → list[dict]  [calls Claude Haiku]

Output schema per item:
  { "title": str, "what": str, "why": str, "url": str, "source": str }
"""

import json
import re
import anthropic


# ── Scoring signals ─────────────────────────────────────────────────────────────

# First-person workflow language — strong signal that someone is describing
# their own practice rather than reporting on a product announcement.
_STRONG_SIGNALS = [
    "i built", "i created", "i made", "i wrote a script", "i automated",
    "i deployed", "i trained", "i fine-tuned", "i use claude", "i use chatgpt",
    "i use gpt", "i use llm", "i use ai", "my workflow", "my prompt",
    "my agent", "my automation", "prompt i use", "i set up", "i've been using",
    "i am using", "i'm using", "here's how i", "how i use", "how i built",
    "how i automated", "we built", "we created", "we automated",
    "i released", "i published", "i open-sourced",
]

# Workflow/practice context — meaningful but not definitive on their own.
_MEDIUM_SIGNALS = [
    "workflow", "automation", "prompt", "agent", "hack", "script",
    "integration", "experiment", "tutorial", "example", "approach",
    "pipeline", "tool i", "tool i use", "trick", "technique",
    "use case", "use-case", "side project", "open source",
]

# Downrank signals — corporate press releases, product launches, funding news.
_DOWNRANK_SIGNALS = [
    "announces", "announced", "raises", "funding", "series a", "series b",
    "valuation", "partnership", "press release", "launching today",
    "available now", "we are excited", "proud to announce",
    "general availability", "ipo", "acqui", "merger",
]

# Sources whose content skews toward practitioner share-outs.
_PRACTITIONER_SOURCES = {
    "simon willison",
    "hacker news frontpage",
    "reddit r/localllama",
    "one useful thing",
    "latent space",
    "chip huyen",
    "sebastian raschka",
    "nathan lambert substack",
}


def score_ai_use_candidate(story: dict) -> float:
    """
    Return a relevance score in [0.0, 1.0] for the 'notable AI uses' angle.

    Scans title + summary + full_text (first 1500 chars) for practitioner signals.
    Pure function — no I/O.
    """
    title   = (story.get("title")    or "").lower()
    summary = (story.get("summary")  or "").lower()
    text    = (story.get("full_text") or "")[:1500].lower()

    combined = f"{title} {summary} {text}"

    score = 0.0

    for sig in _STRONG_SIGNALS:
        if sig in combined:
            score += 2.0

    for sig in _MEDIUM_SIGNALS:
        if sig in combined:
            score += 1.0

    for sig in _DOWNRANK_SIGNALS:
        if sig in combined:
            score -= 1.5

    # Source bonus for feeds that regularly surface practitioner content
    source_lower = (story.get("source") or "").lower()
    if any(ps in source_lower for ps in _PRACTITIONER_SOURCES):
        score += 2.0

    # Normalize: cap at 1.0, floor at 0.0
    # A story needs ~3 positive signals (or source bonus + 1) to clear 0.15
    normalized = min(max(score / 10.0, 0.0), 1.0)
    return normalized


def find_notable_ai_uses(stories: list, api_key: str,
                         min_score: float = 0.15,
                         max_candidates: int = 12,
                         excluded_urls: set = None) -> list:
    """
    Identify and extract notable real-world AI uses from the story pool.

    Steps:
      1. Score all stories with score_ai_use_candidate()
      2. Take up to max_candidates above min_score threshold
      3. Call Claude Haiku to extract 0–3 structured items
      4. Return list of dicts: {title, what, why, url, source}

    Args:
      excluded_urls: URLs already used in the main newsletter sections.
                     Stories matching these are skipped to avoid duplication.

    Returns [] on error or when no qualifying items are found.
    """
    excluded = excluded_urls or set()

    # ── Stage 1: keyword pre-filter ───────────────────────────────────────────
    scored = [
        (score_ai_use_candidate(s), s)
        for s in stories
        if s.get("url") not in excluded
    ]
    candidates = [
        s for score, s in sorted(scored, key=lambda x: x[0], reverse=True)
        if score >= min_score
    ][:max_candidates]

    if not candidates:
        print("  Notable AI Uses: no candidates above threshold — skipping")
        return []

    print(f"  Notable AI Uses: {len(candidates)} candidates → asking Claude Haiku")

    # ── Stage 2: Claude Haiku extraction ─────────────────────────────────────
    client = anthropic.Anthropic(api_key=api_key)

    # Build a compact representation of candidates for the prompt
    candidate_items = []
    for s in candidates:
        item = {
            "title":   s.get("title", ""),
            "source":  s.get("source", ""),
            "url":     s.get("url", ""),
            "summary": s.get("summary", "")[:400],
        }
        if s.get("full_text"):
            item["text_excerpt"] = s["full_text"][:600]
        candidate_items.append(item)

    prompt = f"""You are a personal research assistant helping a developer find genuinely interesting, non-obvious real-world uses of AI.

Below are candidate items from RSS feeds. Your job: identify which ones describe a **real, concrete workflow, experiment, hack, or automation** that someone actually built or uses — not product announcements, not press releases, not generic "AI is changing X" editorials.

STRONG signals of a qualifying item:
- First-person language: "I built", "my workflow", "I use Claude/ChatGPT to", "I automated", "my agent", "prompt I use", "I wrote a script"
- Descriptions of actual setups, scripts, pipelines, or integrations
- Developer experiments, side projects, agent architectures, research workflows
- Unusual or clever integrations that most people haven't seen

DISQUALIFY if:
- It's a product announcement or press release
- It's a company blog post about their own product
- It describes AI capabilities in general without a concrete personal workflow
- It's generic beginner content ("5 ways to use ChatGPT")
- It's funding/acquisition/partnership news

For each qualifying item, extract:
- title: A short, specific title (rewrite vague titles to be concrete)
- what: 2–4 sentences describing exactly what the person did with AI — the specific workflow, tool, approach, or integration
- why: 1 sentence on what makes it clever, novel, or practically inspiring
- url: the original URL
- source: the feed name

Return a JSON array of 0–3 qualifying items. If nothing clearly qualifies, return an empty array [].
Return ONLY valid JSON — no markdown, no explanation.

Candidates:
{json.dumps(candidate_items, indent=2)}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()

        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if arr_match:
            result = json.loads(arr_match.group())
            # Validate basic schema
            valid = [
                item for item in result
                if isinstance(item, dict)
                and item.get("title") and item.get("what") and item.get("url")
            ]
            if valid:
                print(f"  Notable AI Uses: {len(valid)} item(s) extracted")
            else:
                print("  Notable AI Uses: Claude found no qualifying items")
            return valid[:3]

    except Exception as e:
        print(f"  Notable AI Uses: extraction failed — {e}")

    return []
