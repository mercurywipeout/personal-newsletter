"""
Unit tests for clustering.cluster.

Tests cover:
  1. Similar texts cluster together
  2. Dissimilar texts do not cluster
  3. Time window prevents clustering despite text similarity
  4. Representative selection: full_text gate, word count, earlier publish time
  5. SimHash and Hamming distance primitives

Run with:  python3 -m unittest clustering.tests.test_cluster
"""

import unittest
from clustering.cluster import (
    _simhash,
    _hamming_distance,
    _normalize_text,
    _fingerprint,
    _within_time_window,
    _is_earliest_in_cluster,
    score_for_representative,
    cluster_stories,
    SIMILARITY_THRESHOLD,
    TIME_WINDOW_HOURS,
)

# ── Shared test fixtures ───────────────────────────────────────────────────────

# Two articles covering the same event from different outlets.
# Deliberately long and lexically overlapping so SimHash agreement is reliable.
_SAME_EVENT_A = (
    "OpenAI has released GPT-5, a major new language model with significantly "
    "improved reasoning capabilities. The model shows substantial improvements "
    "in coding, mathematics, and scientific reasoning tasks. GPT-5 represents "
    "the next generation of the OpenAI model lineup and is available via API."
)
_SAME_EVENT_B = (
    "OpenAI announces the release of GPT-5, its latest language model featuring "
    "significantly improved reasoning performance. The model demonstrates major "
    "gains in coding and mathematical reasoning benchmarks. GPT-5 is OpenAI's "
    "newest flagship model now accessible through the OpenAI API platform."
)

# A clearly different topic.
_DIFFERENT_EVENT = (
    "Apple has unveiled a new chip for its Mac lineup, claiming forty percent "
    "better performance per watt. The silicon uses a completely new architecture "
    "that dramatically improves energy efficiency. Strong demand is expected for "
    "the updated hardware across the professional and consumer segments."
)


def _make_story(uid, text, published="2026-03-01 10:00", full_text="", feed_id="test-feed"):
    return {
        "source":    "Test Feed",
        "feed_id":   feed_id,
        "title":     text[:80],
        "summary":   text,
        "url":       f"https://example.com/{uid}",
        "published": published,
        "full_text": full_text,
    }


# ── SimHash primitives ─────────────────────────────────────────────────────────
class TestSimHash(unittest.TestCase):

    def test_deterministic(self):
        self.assertEqual(_simhash("hello world"), _simhash("hello world"))

    def test_empty_text_returns_zero(self):
        self.assertEqual(_simhash(""), 0)
        self.assertEqual(_simhash("   "), 0)

    def test_identical_texts_have_zero_distance(self):
        h = _simhash(_SAME_EVENT_A)
        self.assertEqual(_hamming_distance(h, h), 0)

    def test_similar_texts_have_small_distance(self):
        h1 = _simhash(_SAME_EVENT_A)
        h2 = _simhash(_SAME_EVENT_B)
        self.assertLessEqual(_hamming_distance(h1, h2), SIMILARITY_THRESHOLD)

    def test_dissimilar_texts_have_large_distance(self):
        h1 = _simhash(_SAME_EVENT_A)
        h2 = _simhash(_DIFFERENT_EVENT)
        self.assertGreater(_hamming_distance(h1, h2), SIMILARITY_THRESHOLD)

    def test_normalize_strips_punctuation(self):
        self.assertEqual(_normalize_text("Hello, World!"), "hello world")

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(_normalize_text("  too   many   spaces  "), "too many spaces")


class TestHammingDistance(unittest.TestCase):

    def test_identical(self):
        self.assertEqual(_hamming_distance(0b1010, 0b1010), 0)

    def test_one_bit_difference(self):
        self.assertEqual(_hamming_distance(0b1010, 0b1011), 1)

    def test_all_bits_different(self):
        self.assertEqual(_hamming_distance(0, (1 << 64) - 1), 64)


# ── Time window ────────────────────────────────────────────────────────────────
class TestWithinTimeWindow(unittest.TestCase):

    def _story(self, published):
        return {"published": published}

    def test_same_time(self):
        a = self._story("2026-03-01 10:00")
        self.assertTrue(_within_time_window(a, a))

    def test_within_window(self):
        a = self._story("2026-03-01 10:00")
        b = self._story("2026-03-02 10:00")  # 24 h gap
        self.assertTrue(_within_time_window(a, b))

    def test_exactly_at_window_edge(self):
        a = self._story("2026-03-01 10:00")
        b = self._story(f"2026-03-04 10:00")  # 72 h gap
        self.assertTrue(_within_time_window(a, b))

    def test_outside_window(self):
        a = self._story("2026-03-01 10:00")
        b = self._story("2026-03-04 11:00")  # 73 h gap
        self.assertFalse(_within_time_window(a, b))

    def test_unknown_published_assumed_within_window(self):
        a = self._story("recent")
        b = self._story("2026-01-01 00:00")  # very old
        self.assertTrue(_within_time_window(a, b))


# ── cluster_stories() ──────────────────────────────────────────────────────────
class TestClusterStories(unittest.TestCase):

    def test_empty_input(self):
        reps, clusters = cluster_stories([])
        self.assertEqual(reps, [])
        self.assertEqual(clusters, [])

    def test_single_story_forms_singleton_cluster(self):
        story = _make_story("s1", _SAME_EVENT_A)
        reps, clusters = cluster_stories([story])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(reps), 1)
        self.assertEqual(len(clusters[0]["article_ids"]), 1)

    def test_similar_texts_cluster_together(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00"),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 12:00"),
        ]
        reps, clusters = cluster_stories(stories)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["article_ids"]), 2)
        self.assertEqual(len(reps), 1)

    def test_dissimilar_texts_do_not_cluster(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00"),
            _make_story("s2", _DIFFERENT_EVENT, published="2026-03-01 10:00"),
        ]
        reps, clusters = cluster_stories(stories)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(reps), 2)

    def test_time_window_prevents_clustering_of_similar_texts(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00"),
            _make_story("s2", _SAME_EVENT_B, published="2026-02-25 09:00"),  # >72 h earlier
        ]
        reps, clusters = cluster_stories(stories, time_window_hours=72)
        self.assertEqual(len(clusters), 2)

    def test_cluster_contains_correct_urls(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00"),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 11:00"),
        ]
        _, clusters = cluster_stories(stories)
        self.assertEqual(len(clusters), 1)
        ids = set(clusters[0]["article_ids"])
        self.assertIn("https://example.com/s1", ids)
        self.assertIn("https://example.com/s2", ids)

    def test_cluster_domains_populated(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00"),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 11:00"),
        ]
        _, clusters = cluster_stories(stories)
        self.assertIn("example.com", clusters[0]["domains"])

    def test_three_stories_same_event(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 08:00"),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 10:00"),
            _make_story("s3", _SAME_EVENT_A + " additional coverage", published="2026-03-01 12:00"),
        ]
        reps, clusters = cluster_stories(stories)
        self.assertEqual(len(reps), 1)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]["article_ids"]), 3)


# ── score_for_representative() ─────────────────────────────────────────────────
class TestScoreForRepresentative(unittest.TestCase):

    def _story(self, published="2026-03-01 10:00", full_text="", words=0):
        ft = full_text or (("word " * words) if words else "")
        return {
            "published": published,
            "full_text": ft.strip(),
            "url": "https://example.com/x",
        }

    def test_full_text_dominates_no_full_text(self):
        with_text    = self._story(full_text="article content " * 50)
        without_text = self._story(full_text="")
        self.assertGreater(
            score_for_representative(with_text),
            score_for_representative(without_text),
        )

    def test_earlier_publish_preferred_when_both_have_full_text(self):
        earlier = self._story(published="2026-03-01 08:00", full_text="content " * 50)
        later   = self._story(published="2026-03-01 20:00", full_text="content " * 50)
        self.assertGreater(
            score_for_representative(earlier),
            score_for_representative(later),
        )

    def test_higher_word_count_preferred_when_same_publish_time(self):
        more_words = self._story(published="2026-03-01 10:00", words=600)
        fewer_words = self._story(published="2026-03-01 10:00", words=50)
        self.assertGreater(
            score_for_representative(more_words),
            score_for_representative(fewer_words),
        )

    def test_word_count_capped(self):
        """Word count bonus should not grow unboundedly."""
        moderate = self._story(words=800)
        massive  = self._story(words=5000)
        # Both get the full word-count bonus; the cap prevents unbounded growth
        self.assertAlmostEqual(
            score_for_representative(moderate),
            score_for_representative(massive),
            delta=0.1,
        )

    def test_recent_unknown_publish_gets_no_time_bonus(self):
        known   = self._story(published="2026-03-01 08:00", full_text="content " * 50)
        unknown = self._story(published="recent", full_text="content " * 50)
        # Known (earlier) should outscore unknown
        self.assertGreaterEqual(
            score_for_representative(known),
            score_for_representative(unknown),
        )


# ── Representative selection within cluster ────────────────────────────────────
class TestRepresentativeSelection(unittest.TestCase):

    def test_representative_prefers_story_with_full_text(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 10:00", full_text=""),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 12:00",
                        full_text="Full article content here " * 30),
        ]
        reps, clusters = cluster_stories(stories)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["representative_article_id"],
                         "https://example.com/s2")  # s2 has full_text

    def test_representative_prefers_earlier_when_both_have_full_text(self):
        stories = [
            _make_story("s1", _SAME_EVENT_A, published="2026-03-01 08:00",
                        full_text="content about gpt release " * 30),
            _make_story("s2", _SAME_EVENT_B, published="2026-03-01 20:00",
                        full_text="content about gpt release " * 30),
        ]
        reps, clusters = cluster_stories(stories)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["representative_article_id"],
                         "https://example.com/s1")  # s1 is earlier

    def test_singleton_cluster_is_its_own_representative(self):
        story = _make_story("s1", _SAME_EVENT_A)
        reps, clusters = cluster_stories([story])
        self.assertEqual(clusters[0]["representative_article_id"],
                         "https://example.com/s1")


# ── _is_earliest_in_cluster() ──────────────────────────────────────────────────
class TestIsEarliestInCluster(unittest.TestCase):

    def _story(self, uid, published):
        return {"url": f"https://example.com/{uid}", "published": published}

    def test_true_when_story_is_earliest(self):
        s1 = self._story("s1", "2026-03-01 08:00")
        s2 = self._story("s2", "2026-03-01 12:00")
        self.assertTrue(_is_earliest_in_cluster(s1, [s1, s2]))

    def test_false_when_earlier_story_exists(self):
        s1 = self._story("s1", "2026-03-01 12:00")
        s2 = self._story("s2", "2026-03-01 08:00")
        self.assertFalse(_is_earliest_in_cluster(s1, [s1, s2]))

    def test_false_for_unknown_publish_time(self):
        s1 = self._story("s1", "recent")
        s2 = self._story("s2", "2026-03-01 10:00")
        self.assertFalse(_is_earliest_in_cluster(s1, [s1, s2]))

    def test_true_for_singleton(self):
        s1 = self._story("s1", "2026-03-01 10:00")
        self.assertTrue(_is_earliest_in_cluster(s1, [s1]))


if __name__ == "__main__":
    unittest.main()
