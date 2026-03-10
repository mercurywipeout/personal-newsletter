# Newsletter Template Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the AI/tech newsletter into a forkable, self-managing newsletter engine that anyone can point at any topic by filling in a single `config/newsletter.yaml` file.

**Architecture:** All topic-specific language is replaced with template variables loaded from `config/newsletter.yaml` (newsletter name, editorial brief, tone). Claude infers categories dynamically each issue. Feed discovery uses a Claude batch call for relevance scoring instead of hardcoded AI/tech keywords. The `notable_uses` feature is removed. All self-sufficiency systems (clustering, scoring, discovery) stay unchanged.

**Tech Stack:** Python 3.11, anthropic SDK, PyYAML (new dep), feedparser, trafilatura, urllib3, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-09-newsletter-template-design.md`

---

## Chunk 1: Branch + Config Foundation

### Task 1: Create template branch and add PyYAML

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Create the template branch**

```bash
git checkout -b template
```

- [ ] **Step 2: Add PyYAML to requirements.txt**

Add `PyYAML>=6.0` as a new line in `requirements.txt`.

Final file:
```
feedparser==6.0.11
anthropic>=0.40.0
trafilatura>=1.12.0
courlan>=1.3.0
lxml_html_clean>=0.4.0
PyYAML>=6.0
```

- [ ] **Step 3: Install and verify**

```bash
pip install PyYAML>=6.0
python -c "import yaml; print(yaml.__version__)"
```
Expected: a version string printed with no errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add PyYAML dependency for newsletter.yaml config"
```

---

### Task 2: Create `config/newsletter.yaml` and load it in `daily_newsletter.py`

**Files:**
- Create: `config/newsletter.yaml`
- Create: `config/newsletter.yaml.example`
- Modify: `daily_newsletter.py` (add `load_newsletter_config()`, module-level `NEWSLETTER_CONFIG`)

- [ ] **Step 1: Write a failing test for `load_newsletter_config()`**

Create `tests/test_newsletter_config.py`:

```python
"""Tests for newsletter.yaml config loading."""
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open
import sys, os
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLoadNewsletterConfig(unittest.TestCase):

    def test_defaults_when_file_missing(self):
        """Missing newsletter.yaml returns safe defaults."""
        with patch("pathlib.Path.exists", return_value=False):
            # Import fresh to trigger module-level load
            import importlib
            import daily_newsletter as dn
            cfg = dn.load_newsletter_config()
        self.assertEqual(cfg["name"], "Daily Brief")
        self.assertIn("brief", cfg)
        self.assertEqual(cfg["tone"], "executive")

    def test_loads_name_brief_tone(self):
        """Valid newsletter.yaml is parsed correctly."""
        yaml_content = (
            "name: My Newsletter\n"
            "brief: A test brief.\n"
            "tone: analytical\n"
        )
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=yaml_content):
            import daily_newsletter as dn
            cfg = dn.load_newsletter_config()
        self.assertEqual(cfg["name"], "My Newsletter")
        self.assertEqual(cfg["brief"], "A test brief.")
        self.assertEqual(cfg["tone"], "analytical")

    def test_missing_optional_fields_use_defaults(self):
        """YAML missing tone falls back to 'executive'."""
        yaml_content = "name: Test\nbrief: Something.\n"
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=yaml_content):
            import daily_newsletter as dn
            cfg = dn.load_newsletter_config()
        self.assertEqual(cfg["tone"], "executive")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to confirm it fails (function doesn't exist yet)**

```bash
python3 -m unittest tests.test_newsletter_config -v
```
Expected: `AttributeError` or `ImportError` — `load_newsletter_config` does not exist yet.

- [ ] **Step 3: Add `load_newsletter_config()` to `daily_newsletter.py`**

Insert after the existing `load_config()` function (after line 45, before the `_REQUIRED_KEYS` block). Add the import for `yaml` at the top of the file alongside the other imports.

Add at top of file with other imports:
```python
import yaml
```

Insert after `CONFIG = load_config()` (line 45):
```python
# ── Newsletter identity config ──────────────────────────────────────────────────
_NEWSLETTER_YAML_PATH = Path(__file__).parent / "config" / "newsletter.yaml"

_TONE_PROMPTS = {
    "executive":      "Concise and decision-focused. Emphasize what changed and what it means for practitioners. Lead with the most important implication.",
    "analytical":     "Data-driven and nuanced. Surface uncertainty and counterarguments. Distinguish confirmed facts from speculation.",
    "conversational": "Friendly and opinionated, like a smart colleague's message. Direct, with a point of view.",
    "accessible":     "Plain language throughout. Explain jargon when it appears. Assume an intelligent but non-specialist reader.",
}

def load_newsletter_config():
    if not _NEWSLETTER_YAML_PATH.exists():
        return {
            "name":  "Daily Brief",
            "brief": "A daily curation of the most important stories across technology, business, and science.",
            "tone":  "executive",
        }
    data = yaml.safe_load(_NEWSLETTER_YAML_PATH.read_text())
    return {
        "name":  (data.get("name") or "Daily Brief").strip(),
        "brief": (data.get("brief") or "").strip(),
        "tone":  (data.get("tone") or "executive").strip(),
    }

NEWSLETTER_CONFIG = load_newsletter_config()
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
python3 -m unittest tests.test_newsletter_config -v
```
Expected: 3 tests pass.

- [ ] **Step 5: Create `config/newsletter.yaml`**

```yaml
# Newsletter identity — edit these three fields to make this newsletter yours.

# The name of your newsletter (appears in the email header and From address).
name: "Your Newsletter Name"

# An editorial brief: describe your topic, audience, and what counts as
# high-signal for your readers. 2–4 sentences. Claude uses this to shape
# the curation, categories, briefing style, and subject line.
brief: |
  A daily briefing on [your topic here]. Written for [your audience].
  Focuses on [what you care about] — filtering for signal over noise.

# Tone of the newsletter. Options:
#   executive     — concise, decision-focused, leads with implications
#   analytical    — data-driven, surfaces uncertainty and counterarguments
#   conversational — friendly and opinionated, like a smart colleague
#   accessible    — plain language, explains jargon, mixed-expertise audience
# You can also write a free-text description instead of using a preset.
tone: executive
```

- [ ] **Step 6: Create `config/newsletter.yaml.example`**

```yaml
# Example: a climate tech newsletter

name: "The Green Signal"

brief: |
  A daily briefing for founders and investors tracking the intersection of
  climate tech, clean energy policy, and corporate sustainability.
  Emphasizes actionable intelligence — what changed, why it matters,
  what to watch next. Avoids advocacy and hype.

tone: executive
```

- [ ] **Step 7: Commit**

```bash
git add daily_newsletter.py config/newsletter.yaml config/newsletter.yaml.example tests/tests/test_newsletter_config.py
git commit -m "feat: add newsletter.yaml config with name, brief, and tone fields"
```

---

## Chunk 2: Generalize `daily_newsletter.py`

### Task 3: Generalize `curate_with_claude()`

**Files:**
- Modify: `daily_newsletter.py:248-365` (`curate_with_claude`)

The Claude prompt currently:
- Is addressed to "Systems Brief" editor
- Targets AI and tech specifically
- Prescribes three hardcoded categories
- Uses topic-specific subject line framing

The new prompt:
- Uses `NEWSLETTER_CONFIG` values (`name`, `brief`, tone instruction from `_TONE_PROMPTS`)
- Does NOT prescribe categories — Claude creates 2–4 appropriate ones per issue
- Return schema is identical (Claude just returns dynamic category strings per story)

- [ ] **Step 1: Write a failing test for the generalized prompt**

Add to `tests/test_newsletter_config.py`:

```python
class TestCuratePromptUsesConfig(unittest.TestCase):

    def test_prompt_contains_newsletter_name(self):
        """curate_with_claude prompt references the newsletter name from config."""
        import daily_newsletter as dn
        # Patch NEWSLETTER_CONFIG with known values
        with patch.object(dn, "NEWSLETTER_CONFIG", {
            "name": "Climate Signal",
            "brief": "A brief about clean energy.",
            "tone": "analytical",
        }), patch.object(dn, "_TONE_PROMPTS", {
            "analytical": "Be analytical."
        }), patch("anthropic.Anthropic") as MockClient:
            mock_msg = MockClient.return_value.messages.create.return_value
            mock_msg.content = [type("C", (), {"text": '{"subject_line":"","situational_briefing":"","stories":[]}'})()]
            dn.curate_with_claude([])
            call_args = MockClient.return_value.messages.create.call_args
            prompt = call_args[1]["messages"][0]["content"]
        self.assertIn("Climate Signal", prompt)
        self.assertIn("A brief about clean energy.", prompt)
        self.assertIn("Be analytical.", prompt)
        # Must NOT contain hardcoded AI/tech categories
        self.assertNotIn("AI Models & Research", prompt)
        self.assertNotIn("Developer Tools & Infrastructure", prompt)
        self.assertNotIn("Big Tech & Industry Moves", prompt)
```

- [ ] **Step 2: Run to confirm it fails**

```bash
python3 -m unittest tests.test_newsletter_config.TestCuratePromptUsesConfig -v
```
Expected: FAIL — prompt contains hardcoded strings.

- [ ] **Step 3: Rewrite `curate_with_claude()` in `daily_newsletter.py`**

Replace the entire function body (lines 248–365). Keep the function signature `def curate_with_claude(stories):` and the return signature `(subject_line, situational_briefing, stories)`.

```python
def curate_with_claude(stories):
    import anthropic
    client = anthropic.Anthropic(api_key=CONFIG["ANTHROPIC_API_KEY"])

    name    = NEWSLETTER_CONFIG["name"]
    brief   = NEWSLETTER_CONFIG["brief"]
    tone    = NEWSLETTER_CONFIG["tone"]
    tone_instruction = _TONE_PROMPTS.get(tone, tone)  # fallback: use tone string directly

    today_str   = datetime.now().strftime("%B %d, %Y")
    stories_json = json.dumps(stories, indent=2)

    prompt = f"""You are the editor of "{name}", a daily newsletter.

Newsletter brief:
{brief}

Tone instruction:
{tone_instruction}

Today's date: {today_str}

Here are raw stories pulled from RSS feeds in the last 36 hours:

{stories_json}

Your job:

0. Analyze the full set of stories and write a 2–3 sentence situational briefing:
- Summarize what materially changed in the last 36 hours
- Avoid hype or sweeping claims
- Distinguish confirmed developments from speculation
- Do NOT repeat individual story summaries
- Clearly state if signals are fragmented or incremental
- Use **bold** (double asterisks) on the 2–4 most important terms or developments

0.5. Write a concise subject line:
- Reflect the most significant confirmed development
- Use concrete nouns and specific developments (avoid abstractions)
- Avoid hype, drama, urgency language, or emotional framing
- No clickbait, no emojis, no quotation marks
- Max 12 words. Clear > clever.

1. Invent 2–4 category names appropriate to today's stories and the newsletter's topic.
   Categories should be specific to the content, not generic placeholders.

2. Select the 6–8 most valuable stories. Prioritize early signal:
   - Meaningful developments before they are fully mainstream
   - Architecture-level changes over minor features
   - Behavioral or adoption shifts over marketing announcements
   - Credible evidence over hype

3. For each selected story write:
   - A punchy, specific headline (rewrite vague or clickbait titles)
   - A 2–3 sentence summary focused on WHY it matters and what to watch for ({tone_instruction})
   - A signal tag: "Major news", "Worth watching", or "Early signal"
   - The category: one of the 2–4 categories you defined
   - The original source name and URL

Return ONLY a valid JSON object:

{{
  "subject_line": "...",
  "situational_briefing": "2–3 sentence overview here",
  "stories": [
    {{
      "title": "...",
      "summary": "...",
      "signal": "Worth watching",
      "category": "Your Dynamic Category Name",
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
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if obj_match:
        try:
            parsed = json.loads(obj_match.group())
            if "stories" in parsed:
                return (
                    parsed.get("subject_line", ""),
                    parsed.get("situational_briefing", ""),
                    parsed["stories"],
                )
        except Exception:
            pass

    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if arr_match:
        try:
            return "", "", json.loads(arr_match.group())
        except Exception:
            pass

    print("  ⚠  Could not parse Claude response — using raw stories as fallback")
    return "", "", []
```

- [ ] **Step 4: Run the test**

```bash
python3 -m unittest tests.test_newsletter_config -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add daily_newsletter.py tests/test_newsletter_config.py
git commit -m "feat: generalize Claude curation prompt using newsletter.yaml config"
```

---

### Task 4: Generalize `build_html_email()` and remove `notable_uses`

**Files:**
- Modify: `daily_newsletter.py:376-499` (`build_html_email`, `send_email`, `main`)

Changes:
- `build_html_email()`: remove `notable_uses` parameter + HTML block; replace hardcoded "Systems Brief" with `newsletter_name`; generalize fallback briefing text
- `send_email()`: use newsletter name in From header and subject fallback
- `main()`: remove `notable_uses` calls; use newsletter name in console header

- [ ] **Step 1: Write failing tests**

Add to `tests/test_newsletter_config.py`:

```python
class TestBuildHtmlEmail(unittest.TestCase):

    def _make_story(self, **kwargs):
        defaults = {
            "title": "Test Story", "summary": "A summary.",
            "signal": "Worth watching", "category": "Tech",
            "source": "Test Source", "url": "https://example.com",
        }
        defaults.update(kwargs)
        return defaults

    def test_uses_newsletter_name_in_header(self):
        """HTML email header contains the newsletter name, not 'Systems Brief'."""
        import daily_newsletter as dn
        with patch.object(dn, "NEWSLETTER_CONFIG", {"name": "Climate Signal", "brief": "", "tone": "executive"}):
            html = dn.build_html_email([self._make_story()])
        self.assertIn("Climate Signal", html)
        self.assertNotIn("Systems Brief", html)

    def test_no_notable_uses_section(self):
        """build_html_email no longer accepts or renders notable_uses."""
        import daily_newsletter as dn
        import inspect
        sig = inspect.signature(dn.build_html_email)
        self.assertNotIn("notable_uses", sig.parameters)
        # Rendered HTML should not contain AI-specific section title
        html = dn.build_html_email([self._make_story()])
        self.assertNotIn("Noted: AI in Practice", html)

    def test_dynamic_categories_rendered(self):
        """Stories are grouped by their dynamic category field."""
        import daily_newsletter as dn
        stories = [
            self._make_story(category="Energy Policy"),
            self._make_story(title="Story 2", category="Clean Tech Funding"),
        ]
        html = dn.build_html_email(stories)
        self.assertIn("Energy Policy", html)
        self.assertIn("Clean Tech Funding", html)
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m unittest tests.test_newsletter_config.TestBuildHtmlEmail -v
```
Expected: FAIL on notable_uses and "Systems Brief" checks.

- [ ] **Step 3: Update `build_html_email()` signature and body**

Change the function signature from:
```python
def build_html_email(stories, situational_briefing="", notable_uses=None):
```
to:
```python
def build_html_email(stories, situational_briefing=""):
```

In the function body:
- Remove the entire `# ── Notable AI Uses section` block (lines 422–455)
- Replace `"Systems Brief"` in the `<title>` tag with `{escape(NEWSLETTER_CONFIG['name'])}`
- Replace `"Systems Brief"` in the dark header `<div>` with `{escape(NEWSLETTER_CONFIG['name'])}`
- Replace the hardcoded fallback briefing text `"Your daily curation of what&#39;s moving in AI, developer tools, and big tech — filtered for signal over noise."` with `"Your daily curation of the most important stories — filtered for signal over noise."`
- Remove `{notable_html}` from the stories `<div>`

Updated header section:
```python
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(NEWSLETTER_CONFIG['name'])} — {today_short}</title>
</head>
<body style="margin:0;padding:0;background:#f2f1ee;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,sans-serif;">

  <div style="max-width:620px;margin:32px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

    <!-- Header -->
    <div style="background:#0a0a0a;padding:36px 44px 32px;">
      <div style="font-size:10px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:2.5px;margin-bottom:10px;">Daily Intelligence</div>
      <div style="font-size:30px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;line-height:1;">{escape(NEWSLETTER_CONFIG['name'])}</div>
      <div style="margin-top:10px;font-size:13px;color:#888;">{today_long}</div>
    </div>

    <!-- Intro / Situational Briefing -->
    <div style="padding:28px 44px 0;border-bottom:1px solid #f0eeeb;">
      <p style="margin:0 0 34px;font-size:14px;line-height:1.7;color:#555;">
        {render_briefing(situational_briefing) if situational_briefing else "Your daily curation of the most important stories — filtered for signal over noise."}
      </p>
      <div style="height:24px;"></div>
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
```

- [ ] **Step 4: Update `send_email()`**

Replace hardcoded "Systems Brief" references:
```python
def send_email(html_content, subject_line=""):
    name        = NEWSLETTER_CONFIG["name"]
    today_short = datetime.now().strftime("%b %d")
    raw_subject = subject_line if subject_line else f"{name} — {today_short}"
    subject = raw_subject.replace("\r", "").replace("\n", " ").strip()[:200]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{name} <{CONFIG['GMAIL_USER']}>"
    msg["To"]      = CONFIG["RECIPIENT_EMAIL"]
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(CONFIG["GMAIL_USER"], CONFIG["GMAIL_APP_PASSWORD"])
        server.sendmail(CONFIG["GMAIL_USER"], CONFIG["RECIPIENT_EMAIL"], msg.as_string())

    print(f"  ✓ Email sent → {CONFIG['RECIPIENT_EMAIL']}")
```

- [ ] **Step 5: Update `main()` — remove notable_uses, update console header**

In `main()`:
- Remove `from notable_uses import find_notable_ai_uses` (line 18 import at top of file too)
- Remove `notable_uses = find_notable_ai_uses(stories, CONFIG["ANTHROPIC_API_KEY"])` (line 568)
- Change `html = build_html_email(curated, briefing, notable_uses)` → `html = build_html_email(curated, briefing)`
- Replace `"Systems Brief  |  "` in the console header with `f"  {NEWSLETTER_CONFIG['name']}  |  "`

Also remove line 18 at the top of the file:
```python
from notable_uses import find_notable_ai_uses  # DELETE THIS LINE
```

- [ ] **Step 6: Run all tests**

```bash
python3 -m unittest tests.test_newsletter_config -v
python3 -m unittest clustering.tests.test_cluster scoring.tests.test_scorecard discovery.tests.test_discovery -v
```
Expected: all pass.

- [ ] **Step 7: Smoke test with --dry-run**

```bash
python daily_newsletter.py --dry-run
```
Open `newsletter_preview.html` in a browser. Verify: newsletter name appears in the header, no "Systems Brief", no "Noted: AI in Practice" section.

- [ ] **Step 8: Commit**

```bash
git add daily_newsletter.py tests/test_newsletter_config.py
git commit -m "feat: generalize build_html_email and remove notable_uses feature"
```

---

## Chunk 3: Generalize Feed Discovery

### Task 5: Replace `_TECH_KEYWORDS` with Claude batch relevance scoring

**Files:**
- Modify: `discovery/recommend_feeds.py`
- Modify: `discovery/tests/test_discovery.py`

Changes:
- Remove `_TECH_KEYWORDS` and `_OFF_TOPIC_KEYWORDS` constants
- Add `_load_newsletter_brief()` helper
- Add `score_relevance_batch()` — single Claude call for all validated candidates
- Update `score_candidate()` to accept `relevance_adjustment` and `relevance_reason` params
- Restructure `run_recommendations()` to: validate all → batch score → apply
- Remove hardcoded `"category": "ai"` from `_auto_add_feeds()`

- [ ] **Step 1: Write failing tests**

Add to `discovery/tests/test_discovery.py`:

```python
from discovery.recommend_feeds import score_relevance_batch, _load_newsletter_brief

class TestScoreRelevanceBatch(unittest.TestCase):

    def test_returns_empty_on_missing_brief(self):
        """Returns empty dict when brief is empty (no API call made)."""
        result = score_relevance_batch({"example.com": {}}, brief="", api_key="key")
        self.assertEqual(result, {})

    def test_returns_empty_on_missing_api_key(self):
        """Returns empty dict when api_key is empty."""
        result = score_relevance_batch({"example.com": {}}, brief="A brief.", api_key="")
        self.assertEqual(result, {})

    def test_graceful_fallback_on_claude_error(self):
        """Returns empty dict if Claude call raises an exception."""
        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.side_effect = Exception("API error")
            result = score_relevance_batch(
                {"example.com": {"feed_title": "Test"}},
                brief="A brief.", api_key="sk-test"
            )
        self.assertEqual(result, {})

    def test_parses_claude_response(self):
        """Parses Claude JSON response into (adjustment, reason) tuples."""
        response_json = '{"example.com": {"adjustment": 3.0, "reason": "relevant"}}'
        with patch("anthropic.Anthropic") as MockClient:
            mock_msg = MockClient.return_value.messages.create.return_value
            mock_msg.content = [type("C", (), {"text": response_json})()]
            result = score_relevance_batch(
                {"example.com": {"feed_title": "Test Feed"}},
                brief="A brief.", api_key="sk-test"
            )
        self.assertEqual(result["example.com"], (3.0, "relevant"))


class TestScoreCandidateWithRelevanceAdjustment(unittest.TestCase):

    _VALID = {
        "ok": True, "items_sampled": 5, "latest_pub": "2026-03-08",
        "feed_title": "Test", "feed_description": "", "entry_title_sample": [],
    }

    def test_applies_positive_relevance_adjustment(self):
        score, reasons = score_candidate(
            "example.com", "https://example.com/feed", self._VALID, [{}],
            relevance_adjustment=3.0, relevance_reason="relevant"
        )
        self.assertIn("relevant", reasons)
        self.assertGreater(score, 5.0)

    def test_applies_negative_relevance_adjustment(self):
        score, reasons = score_candidate(
            "example.com", "https://example.com/feed", self._VALID, [{}],
            relevance_adjustment=-4.0, relevance_reason="off-topic"
        )
        self.assertIn("off-topic", reasons)

    def test_zero_adjustment_when_not_provided(self):
        """No relevance_adjustment = no tech/off-topic keywords applied."""
        score1, _ = score_candidate(
            "example.com", "https://example.com/feed", self._VALID, [{}]
        )
        # Should not reference _TECH_KEYWORDS or _OFF_TOPIC_KEYWORDS
        from discovery import recommend_feeds as rf
        self.assertFalse(hasattr(rf, "_TECH_KEYWORDS"))
```

- [ ] **Step 2: Run to confirm they fail**

```bash
python3 -m unittest discovery.tests.test_discovery.TestScoreRelevanceBatch discovery.tests.test_discovery.TestScoreCandidateWithRelevanceAdjustment -v
```
Expected: FAIL — functions don't exist yet.

- [ ] **Step 3: Add `import os` to `recommend_feeds.py` (if not present), add `_load_newsletter_brief()`**

Near the top of `discovery/recommend_feeds.py`, after the existing imports, add:
```python
import os
```

After the `_OFF_TOPIC_KEYWORDS` block (line 103), add the new function and **delete** `_TECH_KEYWORDS` and `_OFF_TOPIC_KEYWORDS`:

```python
# ── Newsletter config ───────────────────────────────────────────────────────────

def _load_newsletter_brief() -> str:
    """Load the newsletter brief from config/newsletter.yaml."""
    config_path = _HERE / "config" / "newsletter.yaml"
    if not config_path.exists():
        return ""
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text())
        return (data.get("brief") or "").strip()
    except Exception:
        return ""
```

- [ ] **Step 4: Add `score_relevance_batch()` to `recommend_feeds.py`**

Insert after `_load_newsletter_brief()`:

```python
def score_relevance_batch(candidates: dict, brief: str, api_key: str) -> dict:
    """
    Use Claude to score topical relevance for multiple feed candidates in one call.

    candidates: dict mapping domain -> validation dict (with feed_title, feed_description,
                entry_title_sample fields from validate_feed())
    brief:      newsletter brief string from newsletter.yaml
    api_key:    Anthropic API key

    Returns dict: domain -> (adjustment: float, reason: str)
    Returns {} on any error or missing inputs (caller treats as neutral).
    """
    if not candidates or not brief or not api_key:
        return {}

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    items = [
        {
            "domain":        domain,
            "feed_title":    v.get("feed_title", ""),
            "feed_description": v.get("feed_description", ""),
            "sample_titles": v.get("entry_title_sample", []),
        }
        for domain, v in candidates.items()
    ]

    prompt = f"""You are scoring RSS feeds for topical relevance to a newsletter.

Newsletter brief:
{brief}

For each feed domain below, decide whether it is relevant, off-topic, or neutral
relative to the newsletter's topic.

Respond with:
- "relevant"  → the feed covers the newsletter's topic area (+3.0 adjustment)
- "off-topic" → the feed is clearly unrelated to the newsletter's topic (-4.0 adjustment)
- "neutral"   → unclear or general coverage (0.0 adjustment)

Feeds to score:
{json.dumps(items, indent=2)}

Return ONLY a valid JSON object mapping each domain to its score:
{{
  "domain.com": {{"adjustment": 3.0, "reason": "relevant"}},
  "other.com":  {{"adjustment": -4.0, "reason": "off-topic"}}
}}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        return {
            domain: (float(data["adjustment"]), str(data["reason"]))
            for domain, data in parsed.items()
            if "adjustment" in data and "reason" in data
        }
    except Exception as exc:
        print(f"  ⚠  Claude relevance scoring failed: {exc} — using neutral scores")
        return {}
```

- [ ] **Step 5: Update `score_candidate()` signature**

Replace the topical relevance block (lines 496–508) with a `relevance_adjustment` parameter approach.

New signature:
```python
def score_candidate(
    domain: str,
    feed_url: str,
    validation: dict,
    provenance: list,
    relevance_adjustment: float = 0.0,
    relevance_reason: str = None,
) -> tuple:
```

Replace the topical relevance block at the end of the function (before `return`):
```python
    # Topical relevance (pre-computed by score_relevance_batch)
    if relevance_adjustment != 0.0:
        score += relevance_adjustment
        reasons.append(relevance_reason or ("relevant" if relevance_adjustment > 0 else "off-topic"))

    return round(score, 2), reasons
```

- [ ] **Step 6: Restructure `run_recommendations()` to use batch scoring**

Find the validation loop in `run_recommendations()` (around line 673). Replace the single-pass validate+score loop with a two-pass approach:

```python
    # Step 2: validate feeds and collect results
    validated_results = []   # [(domain, feed_url, validation, provenance)]
    validated_count   = 0
    deadline = time.monotonic() + (GLOBAL_TIME_BUDGET_SECS / 2)

    for domain, provenance in new_domains.items():
        if time.monotonic() > deadline:
            print("  (time budget reached — stopping early)")
            break
        feed_urls = find_feeds_for_domain(domain)
        for feed_url in feed_urls:
            if time.monotonic() > deadline:
                break
            v = validate_feed(feed_url)
            validated_count += 1
            if v.get("ok"):
                validated_results.append((domain, feed_url, v, provenance))
                break  # one valid feed per domain is enough

    print(f"  Tested {validated_count} feed URLs across {len(new_domains)} domains")

    # Step 3: Batch relevance scoring via Claude
    brief   = _load_newsletter_brief()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    rel_input = {domain: v for domain, _, v, _ in validated_results}
    rel_scores = score_relevance_batch(rel_input, brief, api_key)

    # Step 4: Score each validated candidate
    recs = []
    for domain, feed_url, v, provenance in validated_results:
        adj, reason = rel_scores.get(domain, (0.0, "neutral"))
        score, reasons = score_candidate(domain, feed_url, v, provenance, adj, reason)
        if reason == "off-topic":
            _record_rejected_domain(domain)
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
```

Remove the old `validated = 0` counter and `recs = []` declarations that preceded the old loop.

- [ ] **Step 7: Remove hardcoded `"category": "ai"` from `_auto_add_feeds()`**

Find line 614 in `_auto_add_feeds()`:
```python
        seed_batch.append({
            "name":     domain,
            "url":      rec["feed_url"],
            "category": "ai",       # DELETE/CHANGE THIS
            "enabled":  True,
            "priority": 1,
        })
```

Change `"category": "ai"` to `"category": "general"`.

- [ ] **Step 8: Run all tests**

```bash
python3 -m unittest discover -v
```
Expected: all pass (including the new discovery tests).

- [ ] **Step 9: Commit**

```bash
git add discovery/recommend_feeds.py discovery/tests/test_discovery.py
git commit -m "feat: replace keyword-based feed relevance with Claude batch scoring"
```

---

## Chunk 4: Cleanup and Polish

### Task 6: Remove `notable_uses.py`, neutralize `feeds.seed.json`, update docs

**Files:**
- Delete: `notable_uses.py`
- Delete: `test_notable_uses.py`
- Modify: `config/feeds.seed.json`
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `.github/workflows/daily-newsletter.yml`
- Create: `tests/__init__.py` (if it doesn't exist)

- [ ] **Step 1: Delete notable_uses files**

```bash
git rm notable_uses.py test_notable_uses.py
```

- [ ] **Step 2: Replace `config/feeds.seed.json` with neutral example feeds**

These are well-known, topic-agnostic or broadly applicable feeds that serve as good examples:

```json
[
  {
    "name": "Hacker News Frontpage",
    "url": "https://hnrss.org/frontpage",
    "category": "general",
    "enabled": true,
    "priority": 1
  },
  {
    "name": "MIT Technology Review",
    "url": "https://www.technologyreview.com/feed/",
    "category": "general",
    "enabled": true,
    "priority": 1
  },
  {
    "name": "Ars Technica",
    "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "category": "general",
    "enabled": true,
    "priority": 2
  },
  {
    "name": "Reuters Technology",
    "url": "https://feeds.reuters.com/reuters/technologyNews",
    "category": "general",
    "enabled": true,
    "priority": 2
  }
]
```

> **Note for implementer:** Replace feeds.seed.json with only these 4 neutral example entries. The AI/tech-specific feeds belong in the full newsletter's config, not the template.

- [ ] **Step 3: Add prominent schedule comment to the daily workflow**

Open `.github/workflows/daily-newsletter.yml`. Find the `schedule:` block and add a comment:

```yaml
on:
  # ── Schedule ────────────────────────────────────────────────────────────────
  # Runs daily at 15:00 UTC (7 AM PT / 8 AM PDT).
  # To change the delivery time, update the cron expression below.
  # Use https://crontab.guru to generate cron expressions.
  # Format: 'minute hour day-of-month month day-of-week'
  schedule:
    - cron: '0 15 * * *'
  workflow_dispatch:
```

- [ ] **Step 4: Rewrite `README.md`**

Rewrite to be topic-agnostic. Key sections: 5-minute setup, local run, feed management, schedule. Replace all AI/tech-specific language.

```markdown
# Newsletter Engine

A self-managing daily newsletter that fetches stories from RSS feeds, deduplicates and clusters them, curates the best with Claude, and delivers a formatted HTML email every morning.

Configure it with a name, an editorial brief, and a list of RSS feeds — it handles everything else automatically.

## How it works

1. **Fetch** — pulls stories from your RSS feeds (last 36 hours)
2. **Cluster** — groups near-duplicate stories using SimHash; one representative per event
3. **Curate** — sends stories to Claude, which selects 6–8 high-signal items with summaries and a situational briefing
4. **Deliver** — builds a responsive HTML email and sends it via Gmail

Runs automatically every morning via GitHub Actions.

---

## 5-minute setup

### 1. Fork and clone

```bash
git clone https://github.com/your-username/your-fork.git
cd your-fork
pip install -r requirements.txt
```

### 2. Configure your newsletter

Edit `config/newsletter.yaml`:

```yaml
name: "Your Newsletter Name"
brief: |
  Describe your newsletter's topic and audience here.
  Claude uses this to shape curation, categories, and tone.
tone: executive  # executive | analytical | conversational | accessible
```

Edit `config/feeds.seed.json` — replace the example feeds with sources relevant to your topic.

### 3. Add GitHub Secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your [Anthropic API key](https://console.anthropic.com/) |
| `GMAIL_USER` | Gmail address to send from |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `RECIPIENT_EMAIL` | Delivery address |

### 4. Trigger your first run

Go to **Actions → Daily Newsletter → Run workflow**.

---

## Running locally

Create a `newsletter.config` file in the project root (gitignored — never committed):

```
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
RECIPIENT_EMAIL=you@gmail.com
ANTHROPIC_API_KEY=sk-ant-...
```

Then run:

```bash
python daily_newsletter.py          # full run (sends email)
python daily_newsletter.py --dry-run  # preview only, saves newsletter_preview.html
```

---

## Feed management

Feeds are defined in `config/feeds.seed.json`. Each entry has a `name`, `url`, `category`, `enabled`, and `priority`. On first run, this is bootstrapped into `data/feeds.json` (runtime state).

The scorecard system automatically moves low-performing feeds to `probation` or `disabled` based on rolling click and pass-through rates, and recovers feeds that improve.

### Adding feeds

Edit `config/feeds.seed.json`. The `category` field is informational — Claude determines categories dynamically each issue.

### Weekly feed discovery

A separate workflow runs every Sunday. It analyzes outbound links from recent articles, discovers candidate RSS feeds, and uses Claude to score their topical relevance against your newsletter brief. Top candidates are auto-added as probation feeds.

Run it manually:

```bash
python -m discovery.recommend_feeds --day 2026-03-01
python -m discovery.recommend_feeds --day 2026-03-01 --auto-add
```

---

## Changing the delivery schedule

Edit the cron expression in `.github/workflows/daily-newsletter.yml`. Use [crontab.guru](https://crontab.guru) to generate expressions.

---

## Project structure

```
daily_newsletter.py          # main entry point
config/
  newsletter.yaml            # your newsletter identity (name, brief, tone)
  feeds.seed.json            # committed feed definitions
article_fetcher.py           # full-text enrichment (urllib3 + trafilatura)
clustering/                  # SimHash deduplication
scoring/                     # feed scorecard and telemetry
discovery/                   # weekly feed recommendation system
data/                        # runtime state (committed by CI)
.github/workflows/
  daily-newsletter.yml       # daily send
  weekly-feed-discovery.yml  # Sunday feed discovery
```

---

## Requirements

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)
- A Gmail account with an App Password enabled
```

- [ ] **Step 5: Update `CLAUDE.md`**

Update the project overview section to be topic-agnostic. Replace "AI & Tech Signal Newsletter" with "Self-Managing Newsletter Engine". Replace AI/tech-specific descriptions of the pipeline with general ones. Keep the architecture, code style, and testing sections intact — they are already generic.

Specifically update:
- Title: `# CLAUDE.md — Self-Managing Newsletter Engine`
- Project Overview paragraph: replace "AI & tech news" with "stories from RSS feeds on any topic"
- Claude curation description: remove references to AI/tech-specific categories

- [ ] **Step 6: Run full test suite**

```bash
python3 -m unittest discover -v
```
Expected: all pass. `test_notable_uses.py` is gone, so no test collection errors.

- [ ] **Step 7: Final dry-run smoke test**

```bash
python daily_newsletter.py --dry-run
```

Inspect `newsletter_preview.html`. Verify:
- Newsletter name in header
- No "Systems Brief", no "Noted: AI in Practice"
- Stories grouped by dynamically named categories
- Briefing present

- [ ] **Step 8: Commit all cleanup**

```bash
git add config/feeds.seed.json README.md CLAUDE.md .github/workflows/daily-newsletter.yml
git commit -m "feat: replace AI/tech example feeds, update README and CLAUDE.md for general use"
```

- [ ] **Step 9: Push the template branch**

```bash
git push -u origin template
```

---

## Done

The `template` branch is now a generalized, forkable newsletter engine. To publish as a GitHub template repo:

1. Create a new GitHub repo
2. Copy all files from the `template` branch (excluding `.git/`, `.venv/`, runtime data)
3. `git init && git add . && git commit -m "feat: initial newsletter template"`
4. Push and mark the repo as a **Template repository** in Settings
```
