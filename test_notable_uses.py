#!/usr/bin/env python3
"""Unit tests for notable_uses.score_ai_use_candidate (pure function, no I/O)."""

import unittest
from unittest.mock import patch, MagicMock
import json

from notable_uses import score_ai_use_candidate, find_notable_ai_uses


class TestScoreAiUseCandidate(unittest.TestCase):

    def _story(self, title="", summary="", full_text="", source=""):
        return {"title": title, "summary": summary, "full_text": full_text, "source": source}

    # ── Basic signal detection ──────────────────────────────────────────────────

    def test_empty_story_returns_zero(self):
        self.assertEqual(score_ai_use_candidate(self._story()), 0.0)

    def test_none_fields_handled_gracefully(self):
        story = {"title": None, "summary": None, "full_text": None, "source": None}
        self.assertEqual(score_ai_use_candidate(story), 0.0)

    def test_missing_fields_handled_gracefully(self):
        self.assertEqual(score_ai_use_candidate({}), 0.0)

    def test_single_strong_signal_clears_threshold(self):
        story = self._story(title="I built a Claude agent for code review")
        score = score_ai_use_candidate(story)
        self.assertGreater(score, 0.15)

    def test_multiple_strong_signals_score_higher(self):
        story = self._story(
            title="I built an automation",
            summary="My workflow uses Claude. Here's how I use it every day."
        )
        score = score_ai_use_candidate(story)
        self.assertGreater(score, 0.4)

    def test_medium_signals_alone_score_above_threshold(self):
        story = self._story(summary="A step-by-step tutorial on building an automation pipeline with agents")
        score = score_ai_use_candidate(story)
        self.assertGreater(score, 0.15)

    def test_full_text_is_scanned(self):
        # Strong signal only in full_text, not title/summary
        story = self._story(
            title="AI in practice",
            summary="Some thoughts on AI.",
            full_text="I built a script that automates my daily workflow using Claude."
        )
        score = score_ai_use_candidate(story)
        self.assertGreater(score, 0.15)

    def test_full_text_truncated_at_1500_chars(self):
        # Signal buried past the 1500-char truncation point should not count
        padding = "x" * 1501
        story = self._story(full_text=padding + " i built something")
        score_with_buried = score_ai_use_candidate(story)
        story2 = self._story(full_text="i built something")
        score_with_signal = score_ai_use_candidate(story2)
        self.assertGreater(score_with_signal, score_with_buried)

    # ── Downrank signals ────────────────────────────────────────────────────────

    def test_press_release_language_downgrades_score(self):
        corp_story = self._story(
            title="Acme AI Announces General Availability",
            summary="We are excited to announce our Series B funding round. Partnership with BigCorp."
        )
        plain_story = self._story(
            title="New AI tool",
            summary="An overview of a new AI tool."
        )
        self.assertLessEqual(
            score_ai_use_candidate(corp_story),
            score_ai_use_candidate(plain_story)
        )

    def test_downrank_signals_cannot_produce_negative_score(self):
        story = self._story(
            summary="announces raises funding series a valuation partnership press release "
                    "launching today available now we are excited proud to announce "
                    "general availability ipo acqui merger"
        )
        self.assertEqual(score_ai_use_candidate(story), 0.0)

    # ── Source bonus ────────────────────────────────────────────────────────────

    def test_practitioner_source_bonus_applied(self):
        story_generic = self._story(title="How to use AI", source="TechCrunch")
        story_practitioner = self._story(title="How to use AI", source="Simon Willison")
        self.assertGreater(
            score_ai_use_candidate(story_practitioner),
            score_ai_use_candidate(story_generic)
        )

    def test_practitioner_source_case_insensitive(self):
        lower = self._story(source="simon willison")
        upper = self._story(source="Simon Willison")
        self.assertEqual(score_ai_use_candidate(lower), score_ai_use_candidate(upper))

    def test_all_practitioner_sources_match(self):
        sources = [
            "Simon Willison",
            "Hacker News Frontpage",
            "Reddit r/LocalLLaMA",
            "One Useful Thing",
            "Latent Space",
            "Chip Huyen",
            "Sebastian Raschka",
            "Nathan Lambert Substack",
        ]
        generic = self._story(source="VentureBeat AI")
        for source in sources:
            practitioner = self._story(source=source)
            self.assertGreater(
                score_ai_use_candidate(practitioner),
                score_ai_use_candidate(generic),
                msg=f"Source '{source}' should score higher than generic"
            )

    # ── Score bounds ────────────────────────────────────────────────────────────

    def test_score_never_exceeds_1(self):
        story = self._story(
            title="I built I created I made I wrote a script I automated my workflow my prompt",
            summary="I built an agent. My automation. Here's how I use it. I deployed it. I trained.",
            full_text="i built " * 100,
            source="Simon Willison",
        )
        self.assertLessEqual(score_ai_use_candidate(story), 1.0)

    def test_score_never_below_0(self):
        story = self._story(summary="announces " * 20)
        self.assertGreaterEqual(score_ai_use_candidate(story), 0.0)

    def test_score_is_float(self):
        self.assertIsInstance(score_ai_use_candidate(self._story()), float)


class TestFindNotableAiUses(unittest.TestCase):
    """Tests for find_notable_ai_uses — Claude call is mocked."""

    def _make_stories(self, n=5):
        return [
            {
                "title": f"Story {i}",
                "summary": "summary",
                "full_text": "body",
                "source": "Test Feed",
                "url": f"https://example.com/{i}",
            }
            for i in range(n)
        ]

    def _mock_claude_response(self, items):
        msg = MagicMock()
        msg.content = [MagicMock(text=json.dumps(items))]
        return msg

    def test_returns_empty_when_no_candidates(self):
        # Stories with only downrank signals — all score below threshold
        stories = [
            {"title": "Announces funding", "summary": "raises series b", "url": "http://x.com", "source": "X"}
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(result, [])

    @patch("notable_uses.anthropic")
    def test_returns_validated_items_from_claude(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client

        returned_items = [
            {"title": "Cool workflow", "what": "Used Claude to automate deploys.", "why": "Clever integration.", "url": "https://example.com/1", "source": "Simon Willison"},
            {"title": "Agent setup", "what": "Built a multi-step agent pipeline.", "why": "Novel approach.", "url": "https://example.com/2", "source": "Hacker News Frontpage"},
        ]
        client.messages.create.return_value = self._mock_claude_response(returned_items)

        stories = [
            {"title": "I built a Claude automation", "summary": "my workflow for deployments",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": "I built this script."},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Cool workflow")

    @patch("notable_uses.anthropic")
    def test_filters_items_missing_required_fields(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client

        # One item missing 'what', one missing 'url', one valid
        returned_items = [
            {"title": "No what", "url": "https://example.com/1", "source": "X"},
            {"title": "No url", "what": "did stuff", "source": "X"},
            {"title": "Valid", "what": "did stuff properly", "url": "https://example.com/3", "source": "X"},
        ]
        client.messages.create.return_value = self._mock_claude_response(returned_items)

        stories = [
            {"title": "I built a Claude automation", "summary": "my workflow",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": ""},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Valid")

    @patch("notable_uses.anthropic")
    def test_caps_output_at_3_items(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client

        returned_items = [
            {"title": f"Item {i}", "what": "did stuff", "url": f"https://example.com/{i}", "source": "X"}
            for i in range(5)
        ]
        client.messages.create.return_value = self._mock_claude_response(returned_items)

        stories = [
            {"title": "I built automation", "summary": "my workflow",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": ""},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertLessEqual(len(result), 3)

    @patch("notable_uses.anthropic")
    def test_returns_empty_on_claude_exception(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client
        client.messages.create.side_effect = Exception("API error")

        stories = [
            {"title": "I built automation", "summary": "my workflow",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": ""},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(result, [])

    @patch("notable_uses.anthropic")
    def test_returns_empty_on_malformed_json(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client
        msg = MagicMock()
        msg.content = [MagicMock(text="this is not json at all")]
        client.messages.create.return_value = msg

        stories = [
            {"title": "I built automation", "summary": "my workflow",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": ""},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(result, [])

    @patch("notable_uses.anthropic")
    def test_handles_claude_empty_array_response(self, mock_anthropic):
        client = MagicMock()
        mock_anthropic.Anthropic.return_value = client
        client.messages.create.return_value = self._mock_claude_response([])

        stories = [
            {"title": "I built automation", "summary": "my workflow",
             "url": "https://example.com/1", "source": "Simon Willison", "full_text": ""},
        ]
        result = find_notable_ai_uses(stories, api_key="fake")
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
