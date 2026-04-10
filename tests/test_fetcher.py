import pytest
from src.rss_mcp.fetcher import detect_feed_url, parse_rss_items, scrape_links


class TestDetectFeedUrl:
    def test_finds_rss_link_tag(self):
        html = """
        <html><head>
            <link rel="alternate" type="application/rss+xml" href="https://example.com/feed.xml">
        </head><body></body></html>
        """
        assert detect_feed_url("https://example.com", html) == "https://example.com/feed.xml"

    def test_finds_atom_link_tag(self):
        html = """
        <html><head>
            <link rel="alternate" type="application/atom+xml" href="/feed.atom">
        </head><body></body></html>
        """
        assert detect_feed_url("https://example.com", html) == "https://example.com/feed.atom"

    def test_resolves_relative_href(self):
        html = """
        <html><head>
            <link rel="alternate" type="application/rss+xml" href="/blog/feed">
        </head><body></body></html>
        """
        assert detect_feed_url("https://example.com/blog", html) == "https://example.com/blog/feed"

    def test_returns_none_when_no_feed(self):
        html = "<html><head></head><body><p>No feeds here</p></body></html>"
        assert detect_feed_url("https://example.com", html) is None

    def test_ignores_non_feed_link_tags(self):
        html = """
        <html><head>
            <link rel="stylesheet" href="/style.css">
            <link rel="icon" href="/favicon.ico">
        </head><body></body></html>
        """
        assert detect_feed_url("https://example.com", html) is None

    def test_handles_malformed_html(self):
        html = "<html><head><link rel='alternate' type='application/rss+xml' href='/feed'><<<<broken"
        result = detect_feed_url("https://example.com", html)
        assert result == "https://example.com/feed"

    def test_handles_empty_html(self):
        assert detect_feed_url("https://example.com", "") is None

    def test_detects_direct_rss_documents(self):
        rss_xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
          <channel>
            <title>Example Feed</title>
          </channel>
        </rss>
        """
        assert detect_feed_url("https://example.com/feed.xml", rss_xml) == "https://example.com/feed.xml"


class TestParseRssItems:
    def test_extracts_items_from_rss(self):
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <title>Test Feed</title>
            <item>
              <title>First Post</title>
              <link>https://example.com/post-1</link>
            </item>
            <item>
              <title>Second Post</title>
              <link>https://example.com/post-2</link>
            </item>
          </channel>
        </rss>
        """
        items = parse_rss_items(rss_xml)
        assert len(items) == 2
        assert items[0]["title"] == "First Post"
        assert items[0]["link"] == "https://example.com/post-1"
        assert items[1]["title"] == "Second Post"

    def test_extracts_items_from_atom(self):
        atom_xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <link href="https://example.com" rel="alternate"/>
          <title>Test Feed</title>
          <entry>
            <title>Atom Post</title>
            <link href="https://example.com/atom-1"/>
          </entry>
        </feed>
        """
        items = parse_rss_items(atom_xml)
        assert len(items) == 1
        assert items[0]["title"] == "Atom Post"
        assert items[0]["link"] == "https://example.com/atom-1"

    def test_resolves_relative_atom_links_from_feed_base(self):
        atom_xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <link href="https://example.com" rel="alternate"/>
          <title>Test Feed</title>
          <entry>
            <title>Relative Atom Post</title>
            <link href="/atom-2" rel="alternate"/>
          </entry>
        </feed>
        """
        items = parse_rss_items(atom_xml)
        assert len(items) == 1
        assert items[0]["link"] == "https://example.com/atom-2"

    def test_skips_entries_without_link(self):
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <item><title>No Link</title></item>
            <item><title>Has Link</title><link>https://example.com/ok</link></item>
          </channel>
        </rss>
        """
        items = parse_rss_items(rss_xml)
        assert len(items) == 1
        assert items[0]["title"] == "Has Link"

    def test_returns_empty_for_invalid_xml(self):
        items = parse_rss_items("this is not xml at all")
        assert items == []

    def test_returns_empty_for_valid_but_empty_feed(self):
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel><title>Empty Feed</title></channel>
        </rss>
        """
        items = parse_rss_items(rss_xml)
        assert items == []


class TestScrapeLinks:
    def test_extracts_links_from_page(self):
        html = """
        <html><body>
            <a href="https://example.com/post-1">Post 1</a>
            <a href="https://example.com/post-2">Post 2</a>
        </body></html>
        """
        links = scrape_links("https://example.com", html)
        assert {"https://example.com/post-1", "https://example.com/post-2"}.issubset(
            {l["link"] for l in links}
        )

    def test_resolves_relative_links(self):
        html = """
        <html><body>
            <a href="/blog/post-1">Post 1</a>
        </body></html>
        """
        links = scrape_links("https://example.com", html)
        assert any(l["link"] == "https://example.com/blog/post-1" for l in links)

    def test_extracts_link_text_as_title(self):
        html = """
        <html><body>
            <a href="https://example.com/post-1">My Great Post</a>
        </body></html>
        """
        links = scrape_links("https://example.com", html)
        match = [l for l in links if l["link"] == "https://example.com/post-1"]
        assert match[0]["title"] == "My Great Post"

    def test_filters_out_navigation_and_boilerplate_links(self):
        html = """
        <html><body>
            <a href="/">Home</a>
            <a href="/about">About</a>
            <a href="/contact">Contact</a>
            <a href="/privacy">Privacy</a>
            <a href="/blog">Blog</a>
            <a href="/rss.xml">RSS</a>
            <a href="#section">Jump</a>
            <a href="mailto:test@example.com">Email</a>
            <a href="javascript:void(0)">Click</a>
            <a href="tel:+15551212">Call</a>
            <a href="https://example.com/real-article">Real Article</a>
        </body></html>
        """
        links = scrape_links("https://example.com", html)
        hrefs = {l["link"] for l in links}
        assert "https://example.com/real-article" in hrefs
        assert "https://example.com/about" not in hrefs
        assert "https://example.com/contact" not in hrefs
        assert "https://example.com/privacy" not in hrefs
        assert "https://example.com/blog" not in hrefs
        assert "https://example.com/rss.xml" not in hrefs
        assert "mailto:test@example.com" not in hrefs
        assert "javascript:void(0)" not in hrefs
        assert "tel:+15551212" not in hrefs
        # Fragment-only links should be excluded
        assert "#section" not in hrefs

    def test_strips_fragments_and_deduplicates_equivalent_urls(self):
        html = """
        <html><body>
            <a href="https://example.com/post-1#comments">Post 1</a>
            <a href="https://example.com/post-1">Post 1 Again</a>
        </body></html>
        """
        links = scrape_links("https://example.com", html)
        urls = [l["link"] for l in links]
        assert urls.count("https://example.com/post-1") == 1

    def test_empty_page_returns_no_links(self):
        html = "<html><body><p>No links here</p></body></html>"
        links = scrape_links("https://example.com", html)
        assert links == []
