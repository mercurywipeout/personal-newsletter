"""
Unit tests for scoring.scorecard.

Tests cover:
  - slug()
  - compute_rolling_score()
  - run_pruning_transitions()
  - get_rate_limit()

All tests are self-contained (no file I/O).
Run with:  python -m unittest scoring.tests.test_scorecard
"""

import unittest
from scoring.scorecard import (
    slug,
    compute_rolling_score,
    run_pruning_transitions,
    get_rate_limit,
    POOR_THRESHOLD,
    GOOD_THRESHOLD,
    MIN_DAYS_FOR_SCORE,
    POOR_DAYS_TO_PROBATION,
    POOR_DAYS_TO_DISABLED,
    RECOVERY_DAYS,
    WINDOW_DAYS,
)
from datetime import datetime, timedelta


# ── Helpers ────────────────────────────────────────────────────────────────────
def _make_day_data(feed_id, stories_fetched=10, stories_passed=8,
                   article_ok=5, article_error=0, article_blocked=0):
    return {
        feed_id: {
            "stories_fetched":  stories_fetched,
            "stories_passed":   stories_passed,
            "article_ok":       article_ok,
            "article_error":    article_error,
            "article_blocked":  article_blocked,
        }
    }


def _days_of_stats(feed_id, n_days, day_data_kwargs, anchor="2026-01-20"):
    """Build a daily_stats dict with n_days of identical data ending on anchor."""
    anchor_dt = datetime.strptime(anchor, "%Y-%m-%d")
    stats = {}
    for i in range(n_days):
        day = (anchor_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        stats[day] = _make_day_data(feed_id, **day_data_kwargs)
    return stats


def _make_feed(feed_id="test-feed", status="active",
               consecutive_poor_days=0, good_days_streak=0):
    return {
        "id":                    feed_id,
        "name":                  "Test Feed",
        "url":                   "https://example.com/feed",
        "status":                status,
        "added_at":              "2026-01-01",
        "consecutive_poor_days": consecutive_poor_days,
        "good_days_streak":      good_days_streak,
        "last_transition":       None,
    }


# ── slug() ─────────────────────────────────────────────────────────────────────
class TestSlug(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(slug("OpenAI News"), "openai-news")

    def test_collapses_special_chars(self):
        self.assertEqual(slug("Ars Technica AI!"), "ars-technica-ai")

    def test_numbers_preserved(self):
        self.assertEqual(slug("r/LocalLLaMA"), "r-localllama")

    def test_strips_leading_trailing_hyphens(self):
        self.assertEqual(slug("--weird feed--"), "weird-feed")

    def test_already_lowercase(self):
        self.assertEqual(slug("simon-willison"), "simon-willison")


# ── get_rate_limit() ───────────────────────────────────────────────────────────
class TestGetRateLimit(unittest.TestCase):

    def test_active(self):
        self.assertEqual(get_rate_limit("active"), 8)

    def test_probation(self):
        self.assertEqual(get_rate_limit("probation"), 3)

    def test_disabled(self):
        self.assertEqual(get_rate_limit("disabled"), 0)

    def test_unknown_defaults_to_active(self):
        self.assertEqual(get_rate_limit("mystery"), 8)


# ── compute_rolling_score() ────────────────────────────────────────────────────
class TestComputeRollingScore(unittest.TestCase):

    TODAY = "2026-01-20"
    FEED  = "test-feed"

    def test_no_data_returns_none(self):
        score, days = compute_rolling_score(self.FEED, {}, today=self.TODAY)
        self.assertIsNone(score)
        self.assertEqual(days, 0)

    def test_insufficient_days_returns_none(self):
        stats = _days_of_stats(self.FEED, MIN_DAYS_FOR_SCORE - 1,
                               {"stories_fetched": 10, "stories_passed": 8},
                               anchor=self.TODAY)
        score, days = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        self.assertIsNone(score)
        self.assertEqual(days, MIN_DAYS_FOR_SCORE - 1)

    def test_perfect_feed_high_score(self):
        # All stories pass, no article blocks
        stats = _days_of_stats(self.FEED, 5, {
            "stories_fetched": 10, "stories_passed": 10,
            "article_ok": 5, "article_error": 0, "article_blocked": 0,
        }, anchor=self.TODAY)
        score, days = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        self.assertIsNotNone(score)
        # yield_rate=1.0, block_rate=0 → score = 0.7
        self.assertAlmostEqual(score, 0.7, places=2)
        self.assertEqual(days, 5)

    def test_poor_yield_low_score(self):
        # No stories pass
        stats = _days_of_stats(self.FEED, 5, {
            "stories_fetched": 10, "stories_passed": 0,
            "article_ok": 5, "article_error": 0, "article_blocked": 0,
        }, anchor=self.TODAY)
        score, _ = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        self.assertIsNotNone(score)
        self.assertLess(score, POOR_THRESHOLD)

    def test_high_block_rate_reduces_score(self):
        # Good yield but high block rate
        stats = _days_of_stats(self.FEED, 5, {
            "stories_fetched": 10, "stories_passed": 10,
            "article_ok": 0, "article_error": 0, "article_blocked": 10,
        }, anchor=self.TODAY)
        score, _ = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        # yield=1.0, block=1.0 → 0.7*1.0 - 0.3*1.0 = 0.4
        self.assertIsNotNone(score)
        self.assertAlmostEqual(score, 0.4, places=2)

    def test_score_clamped_to_zero(self):
        # Impossible to go below 0
        stats = _days_of_stats(self.FEED, 5, {
            "stories_fetched": 0, "stories_passed": 0,
            "article_ok": 0, "article_error": 0, "article_blocked": 10,
        }, anchor=self.TODAY)
        score, _ = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, 0.0)

    def test_out_of_window_data_ignored(self):
        # Data older than WINDOW_DAYS should not count
        anchor_dt = datetime.strptime(self.TODAY, "%Y-%m-%d")
        old_day = (anchor_dt - timedelta(days=WINDOW_DAYS + 5)).strftime("%Y-%m-%d")
        stats = {old_day: _make_day_data(self.FEED)}
        score, days = compute_rolling_score(self.FEED, stats, today=self.TODAY)
        self.assertIsNone(score)
        self.assertEqual(days, 0)


# ── run_pruning_transitions() ──────────────────────────────────────────────────
class TestPruningTransitions(unittest.TestCase):

    TODAY = "2026-01-20"
    FEED  = "test-feed"

    def _poor_stats(self, n_days):
        return _days_of_stats(self.FEED, n_days, {
            "stories_fetched": 10, "stories_passed": 0,
            "article_ok": 0, "article_error": 0, "article_blocked": 0,
        }, anchor=self.TODAY)

    def _good_stats(self, n_days):
        return _days_of_stats(self.FEED, n_days, {
            "stories_fetched": 10, "stories_passed": 9,
            "article_ok": 5, "article_error": 0, "article_blocked": 0,
        }, anchor=self.TODAY)

    def test_active_with_good_score_stays_active(self):
        feed  = _make_feed(self.FEED, status="active")
        stats = self._good_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "active")

    def test_active_poor_below_threshold_stays_active_until_limit(self):
        # 5 poor days — not enough to trigger probation yet
        feed = _make_feed(self.FEED, status="active", consecutive_poor_days=5)
        stats = self._poor_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "active")

    def test_active_transitions_to_probation(self):
        # Already at POOR_DAYS_TO_PROBATION - 1, one more poor day tips it
        feed = _make_feed(
            self.FEED,
            status="active",
            consecutive_poor_days=POOR_DAYS_TO_PROBATION - 1,
        )
        stats = self._poor_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "probation")

    def test_probation_transitions_to_disabled(self):
        # Already at combined threshold - 1, one more poor day → disabled
        threshold = POOR_DAYS_TO_PROBATION + POOR_DAYS_TO_DISABLED - 1
        feed = _make_feed(
            self.FEED,
            status="probation",
            consecutive_poor_days=threshold,
        )
        stats = self._poor_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "disabled")

    def test_probation_recovers_to_active(self):
        feed = _make_feed(
            self.FEED,
            status="probation",
            good_days_streak=RECOVERY_DAYS - 1,
        )
        stats = self._good_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "active")
        self.assertEqual(feed["consecutive_poor_days"], 0)

    def test_disabled_recovers_to_active(self):
        feed = _make_feed(
            self.FEED,
            status="disabled",
            good_days_streak=RECOVERY_DAYS - 1,
        )
        stats = self._good_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["status"], "active")

    def test_poor_days_reset_on_good_score(self):
        feed = _make_feed(self.FEED, status="active", consecutive_poor_days=10)
        stats = self._good_stats(5)
        run_pruning_transitions([feed], stats, today=self.TODAY)
        self.assertEqual(feed["consecutive_poor_days"], 0)

    def test_no_data_skips_transition(self):
        feed = _make_feed(self.FEED, status="active", consecutive_poor_days=13)
        # No data → score is None → no transition
        run_pruning_transitions([feed], {}, today=self.TODAY)
        self.assertEqual(feed["status"], "active")
        self.assertEqual(feed["consecutive_poor_days"], 13)  # unchanged


if __name__ == "__main__":
    unittest.main()
