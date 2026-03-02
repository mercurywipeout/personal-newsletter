"""
Unit tests for discovery.recommend_feeds.

All tests are network-free; HTTP calls are mocked with unittest.mock.patch.

Run with:  python3 -m unittest discovery.tests.test_discovery
"""

import unittest
from unittest.mock import patch

from discovery.recommend_feeds import (
    _normalized_domain,
    _is_blocked_domain,
    _extract_links_from_html,
    _extract_feed_links_from_html,
    find_feeds_for_domain,
    validate_feed,
    score_candidate,
)

# ── Fixture HTML/RSS strings ───────────────────────────────────────────────────

_HTML_WITH_LINKS = """
<html><body>
  <a href="https://example.com/same">Same domain</a>
  <a href="https://techblog.io/article">Tech article</a>
  <a href="https://twitter.com/foo">Twitter</a>
  <a href="/relative/path">Relative</a>
  <a href="https://newsletter.ai/post-1">Newsletter AI</a>
  <a href="https://newsletter.ai/post-2">Newsletter AI again</a>
</body></html>
"""

_HTML_WITH_FEED_LINKS = """
<html>
<head>
  <link rel="alternate" type="application/rss+xml"
        href="https://techblog.io/feed.xml" title="Tech Blog RSS">
  <link rel="alternate" type="application/atom+xml"
        href="https://techblog.io/atom.xml" title="Tech Blog Atom">
  <link rel="alternate" type="application/feed+json"
        href="https://techblog.io/feed.json" title="Tech Blog JSON">
  <link rel="stylesheet" href="/style.css">
</head>
<body><p>Hello</p></body>
</html>
"""

_HTML_NO_FEED_LINKS = "<html><head><title>No feed</title></head><body/></html>"

_VALID_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://techblog.io</link>
    <item>
      <title>Article 1</title>
      <link>https://techblog.io/1</link>
      <pubDate>Sun, 01 Mar 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Article 2</title>
      <link>https://techblog.io/2</link>
    </item>
    <item>
      <title>Article 3</title>
      <link>https://techblog.io/3</link>
    </item>
    <item>
      <title>Article 4</title>
      <link>https://techblog.io/4</link>
    </item>
  </channel>
</rss>"""

_VALID_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Post 1</title>
    <link href="https://techblog.io/p1"/>
    <updated>2026-03-01T10:00:00Z</updated>
  </entry>
  <entry>
    <title>Post 2</title>
    <link href="https://techblog.io/p2"/>
  </entry>
  <entry>
    <title>Post 3</title>
    <link href="https://techblog.io/p3"/>
  </entry>
</feed>"""

_TOO_FEW_ENTRIES_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Tiny Feed</title>
    <link>https://sparse.io</link>
    <item><title>Only one</title><link>https://sparse.io/1</link></item>
  </channel>
</rss>"""


# ── TestDomainHelpers ──────────────────────────────────────────────────────────

class TestDomainHelpers(unittest.TestCase):

    def test_normalized_domain_strips_www(self):
        self.assertEqual(_normalized_domain("https://www.example.com/path"), "example.com")

    def test_normalized_domain_lowercase(self):
        self.assertEqual(_normalized_domain("https://TechBlog.IO/post"), "techblog.io")

    def test_normalized_domain_strips_port(self):
        self.assertEqual(_normalized_domain("https://example.com:8080/x"), "example.com")

    def test_normalized_domain_empty_on_bad_url(self):
        self.assertEqual(_normalized_domain("not-a-url"), "")

    def test_is_blocked_social_domains(self):
        for d in ("twitter.com", "x.com", "facebook.com", "youtube.com", "instagram.com"):
            self.assertTrue(_is_blocked_domain(d), f"expected {d} to be blocked")

    def test_is_blocked_tracking_keyword(self):
        self.assertTrue(_is_blocked_domain("clicks.example.com"))
        self.assertTrue(_is_blocked_domain("tracking-stuff.io"))

    def test_is_not_blocked_legitimate_domain(self):
        for d in ("techblog.io", "substack.com", "newsletter.ai", "simonwillison.net"):
            self.assertFalse(_is_blocked_domain(d), f"expected {d} not to be blocked")


# ── TestHtmlExtraction ─────────────────────────────────────────────────────────

class TestHtmlExtraction(unittest.TestCase):

    BASE = "https://example.com/article"

    def test_extracts_absolute_external_links(self):
        links = _extract_links_from_html(_HTML_WITH_LINKS, self.BASE)
        urls = [u for u, _ in links]
        self.assertIn("https://techblog.io/article", urls)
        self.assertIn("https://newsletter.ai/post-1", urls)

    def test_resolves_relative_links(self):
        links = _extract_links_from_html(_HTML_WITH_LINKS, self.BASE)
        urls  = [u for u, _ in links]
        self.assertIn("https://example.com/relative/path", urls)

    def test_captures_anchor_text(self):
        links = _extract_links_from_html(_HTML_WITH_LINKS, self.BASE)
        by_url = {u: t for u, t in links}
        self.assertEqual(by_url.get("https://techblog.io/article"), "Tech article")

    def test_empty_html_returns_empty(self):
        self.assertEqual(_extract_links_from_html("", self.BASE), [])

    def test_extracts_rss_feed_link(self):
        feeds = _extract_feed_links_from_html(_HTML_WITH_FEED_LINKS, "https://techblog.io/")
        urls  = [u for u, _ in feeds]
        self.assertIn("https://techblog.io/feed.xml", urls)

    def test_extracts_atom_feed_link(self):
        feeds = _extract_feed_links_from_html(_HTML_WITH_FEED_LINKS, "https://techblog.io/")
        types = {u: t for u, t in feeds}
        self.assertEqual(types.get("https://techblog.io/atom.xml"), "atom")

    def test_extracts_json_feed_link(self):
        feeds = _extract_feed_links_from_html(_HTML_WITH_FEED_LINKS, "https://techblog.io/")
        types = {u: t for u, t in feeds}
        self.assertEqual(types.get("https://techblog.io/feed.json"), "json")

    def test_ignores_non_alternate_links(self):
        feeds = _extract_feed_links_from_html(_HTML_WITH_FEED_LINKS, "https://techblog.io/")
        urls  = [u for u, _ in feeds]
        self.assertNotIn("https://techblog.io/style.css", urls)

    def test_no_feed_links_returns_empty(self):
        feeds = _extract_feed_links_from_html(_HTML_NO_FEED_LINKS, "https://techblog.io/")
        self.assertEqual(feeds, [])


# ── TestFeedDiscovery ──────────────────────────────────────────────────────────

class TestFeedDiscovery(unittest.TestCase):

    @patch("discovery.recommend_feeds._fetch_html")
    def test_returns_feed_urls_from_html_head(self, mock_fetch):
        mock_fetch.return_value = (_HTML_WITH_FEED_LINKS, 200)
        feeds = find_feeds_for_domain("techblog.io")
        self.assertIn("https://techblog.io/feed.xml", feeds)

    @patch("discovery.recommend_feeds._fetch_html")
    def test_falls_back_to_common_paths_when_no_head_links(self, mock_fetch):
        mock_fetch.return_value = (_HTML_NO_FEED_LINKS, 200)
        feeds = find_feeds_for_domain("techblog.io")
        # Should contain standard probe paths
        self.assertTrue(any("/feed" in f or "/rss" in f for f in feeds))

    @patch("discovery.recommend_feeds._fetch_html")
    def test_falls_back_to_common_paths_on_fetch_failure(self, mock_fetch):
        mock_fetch.return_value = (None, 404)
        feeds = find_feeds_for_domain("techblog.io")
        self.assertTrue(len(feeds) > 0)
        self.assertTrue(any("techblog.io" in f for f in feeds))

    @patch("discovery.recommend_feeds._fetch_html")
    def test_substack_domain_prepends_feed_path(self, mock_fetch):
        # Return no feed links from homepage so we fall to path probing
        mock_fetch.return_value = (_HTML_NO_FEED_LINKS, 200)
        feeds = find_feeds_for_domain("someauthor.substack.com")
        # /feed should be the first candidate for Substack
        self.assertEqual(feeds[0], "https://someauthor.substack.com/feed")

    @patch("discovery.recommend_feeds._fetch_html")
    def test_respects_max_feed_urls(self, mock_fetch):
        mock_fetch.return_value = (_HTML_NO_FEED_LINKS, 200)
        from discovery.recommend_feeds import MAX_FEED_URLS_TESTED_PER_DOMAIN
        feeds = find_feeds_for_domain("techblog.io")
        self.assertLessEqual(len(feeds), MAX_FEED_URLS_TESTED_PER_DOMAIN)


# ── TestFeedValidation ─────────────────────────────────────────────────────────

class TestFeedValidation(unittest.TestCase):

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_valid_rss_feed(self, mock_fetch):
        mock_fetch.return_value = (_VALID_RSS, 200)
        result = validate_feed("https://techblog.io/feed.xml")
        self.assertTrue(result["ok"])
        self.assertEqual(result["feed_type"], "rss")
        self.assertGreaterEqual(result["items_sampled"], 3)

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_valid_atom_feed_detected(self, mock_fetch):
        mock_fetch.return_value = (_VALID_ATOM, 200)
        result = validate_feed("https://techblog.io/atom.xml")
        self.assertTrue(result["ok"])
        self.assertEqual(result["feed_type"], "atom")

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_latest_pub_extracted(self, mock_fetch):
        mock_fetch.return_value = (_VALID_RSS, 200)
        result = validate_feed("https://techblog.io/feed.xml")
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["latest_pub"])
        # latest_pub should be a YYYY-MM-DD string
        self.assertRegex(result["latest_pub"], r"^\d{4}-\d{2}-\d{2}$")

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_too_few_entries_returns_not_ok(self, mock_fetch):
        mock_fetch.return_value = (_TOO_FEW_ENTRIES_RSS, 200)
        result = validate_feed("https://sparse.io/rss")
        self.assertFalse(result["ok"])
        self.assertIn("too_few_entries", result.get("error", ""))

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_http_error_returns_not_ok(self, mock_fetch):
        mock_fetch.return_value = (None, 404)
        result = validate_feed("https://gone.example.com/feed")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 404)

    @patch("discovery.recommend_feeds._fetch_bytes")
    def test_fetch_failure_returns_not_ok(self, mock_fetch):
        mock_fetch.return_value = (None, "connection_error")
        result = validate_feed("https://unreachable.example.com/feed")
        self.assertFalse(result["ok"])


# ── TestScoring ────────────────────────────────────────────────────────────────

class TestScoring(unittest.TestCase):

    def _provenance(self, n=1):
        return [{"source_url": f"https://example.com/{i}", "anchor_text": ""}
                for i in range(n)]

    def _validation(self, items=10, age_days=3):
        from datetime import date, timedelta
        pub = (date.today() - timedelta(days=age_days)).strftime("%Y-%m-%d")
        return {"ok": True, "feed_type": "rss", "items_sampled": items, "latest_pub": pub}

    def test_invalid_feed_scores_zero(self):
        v = {"ok": False, "error": "too_few_entries:1"}
        score, reasons = score_candidate("example.com", "https://example.com/feed", v, [])
        self.assertEqual(score, 0.0)

    def test_valid_active_feed_scores_well(self):
        score, reasons = score_candidate(
            "techblog.io", "https://techblog.io/feed",
            self._validation(items=10, age_days=2),
            self._provenance(1),
        )
        # 5 (base) + 2 (items) + 3 (active) = 10
        self.assertGreaterEqual(score, 9.0)
        self.assertTrue(any("active" in r for r in reasons))

    def test_stale_feed_penalised(self):
        score, reasons = score_candidate(
            "old.io", "https://old.io/rss",
            self._validation(items=10, age_days=200),
            self._provenance(1),
        )
        self.assertTrue(any("stale" in r for r in reasons))
        # Should be penalised relative to active feed
        active_score, _ = score_candidate(
            "active.io", "https://active.io/rss",
            self._validation(items=10, age_days=2),
            self._provenance(1),
        )
        self.assertLess(score, active_score)

    def test_multiple_references_boost_score(self):
        single_ref, _ = score_candidate(
            "techblog.io", "https://techblog.io/feed",
            self._validation(), self._provenance(1),
        )
        multi_ref, reasons = score_candidate(
            "techblog.io", "https://techblog.io/feed",
            self._validation(), self._provenance(3),
        )
        self.assertGreater(multi_ref, single_ref)
        self.assertTrue(any("referenced" in r for r in reasons))

    def test_blog_path_bonus(self):
        blog_score, reasons = score_candidate(
            "techblog.io", "https://techblog.io/blog/feed",
            self._validation(), self._provenance(1),
        )
        generic_score, _ = score_candidate(
            "techblog.io", "https://techblog.io/feed",
            self._validation(), self._provenance(1),
        )
        self.assertGreater(blog_score, generic_score)
        self.assertTrue(any("blog" in r for r in reasons))

    def test_reasons_are_non_empty_strings(self):
        _, reasons = score_candidate(
            "techblog.io", "https://techblog.io/feed",
            self._validation(), self._provenance(2),
        )
        self.assertTrue(all(isinstance(r, str) and r for r in reasons))


if __name__ == "__main__":
    unittest.main()
