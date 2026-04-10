import pytest
from unittest.mock import patch, AsyncMock
from src.rss_mcp.server import add_site, remove_site, list_sites, check_new


@pytest.fixture
def db(tmp_path):
    """Patch the server to use a temp database."""
    from src.rss_mcp.storage import Storage
    storage = Storage(tmp_path / "test.db")
    with patch("src.rss_mcp.server.get_storage", return_value=storage):
        yield storage


class TestAddSite:
    @pytest.mark.anyio
    async def test_add_site_with_rss_feed(self, db):
        """When a site has an RSS feed, detect it and store items."""
        fake_html = """
        <html><head>
            <link rel="alternate" type="application/rss+xml" href="https://example.com/feed.xml">
        </head></html>
        """
        fake_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
            <item><title>Post 1</title><link>https://example.com/post-1</link></item>
        </channel></rss>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=fake_html), \
             patch("src.rss_mcp.server.fetch_feed_xml", new_callable=AsyncMock, return_value=fake_rss):
            result = await add_site("https://example.com", "Example")

        assert "Example" in result
        sites = db.list_sites()
        assert len(sites) == 1
        assert sites[0]["feed_url"] == "https://example.com/feed.xml"

    @pytest.mark.anyio
    async def test_add_site_with_direct_feed_url(self, db):
        """A raw feed URL should be treated as a feed, not scraped as HTML."""
        fake_feed = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
            <item><title>Post 1</title><link>https://example.com/post-1</link></item>
        </channel></rss>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=fake_feed):
            result = await add_site("https://example.com/feed.xml", "Example")

        assert "RSS feed" in result
        sites = db.list_sites()
        assert len(sites) == 1
        assert sites[0]["feed_url"] == "https://example.com/feed.xml"
        rows = db._db.execute("SELECT link, notified FROM items").fetchall()
        assert rows[0]["link"] == "https://example.com/post-1"
        assert rows[0]["notified"] == 1

    @pytest.mark.anyio
    async def test_add_site_without_rss_scrapes_baseline(self, db):
        """When no RSS, scrape links as baseline (marked notified)."""
        fake_html = """
        <html><body>
            <a href="https://example.com/post-1">Post 1</a>
            <a href="https://example.com/post-2">Post 2</a>
            <a href="https://example.com/post-3">Post 3</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=fake_html):
            result = await add_site("https://example.com", "Example")

        assert "Example" in result
        # Baseline items should NOT show up as new
        items = db.get_new_items()
        assert items == []

    @pytest.mark.anyio
    async def test_add_site_fetch_failure(self, db):
        """When the initial fetch fails, return an error message."""
        import httpx
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock,
                   side_effect=httpx.HTTPError("connection refused")):
            result = await add_site("https://down.example.com", "Down Site")
        assert "failed" in result.lower()
        assert db.list_sites() == []

    @pytest.mark.anyio
    async def test_add_duplicate_site(self, db):
        fake_html = "<html><body></body></html>"
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=fake_html):
            await add_site("https://example.com", "Example")
            result = await add_site("https://example.com", "Example")
        assert "already" in result.lower()


class TestRemoveSite:
    @pytest.mark.anyio
    async def test_remove_existing(self, db):
        db.add_site("https://example.com", "Example")
        result = await remove_site("https://example.com")
        assert "removed" in result.lower()
        assert db.list_sites() == []

    @pytest.mark.anyio
    async def test_remove_nonexistent(self, db):
        result = await remove_site("https://nonexistent.com")
        assert "not found" in result.lower()


class TestListSites:
    @pytest.mark.anyio
    async def test_list_empty(self, db):
        result = await list_sites()
        assert "no sites" in result.lower()

    @pytest.mark.anyio
    async def test_list_with_sites(self, db):
        db.add_site("https://example.com", "Example")
        db.add_site("https://other.com", "Other")
        result = await list_sites()
        assert "Example" in result
        assert "Other" in result

    @pytest.mark.anyio
    async def test_list_shows_method(self, db):
        db.add_site("https://rss-site.com", "RSS Site", feed_url="https://rss-site.com/feed")
        db.add_site("https://scraped-site.com", "Scraped Site")
        result = await list_sites()
        assert "RSS" in result
        assert "scraping" in result


class TestCheckNew:
    @pytest.mark.anyio
    async def test_check_new_returns_new_items(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "New Post", "link": "https://example.com/new"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "New Post",
            "link": "https://example.com/new",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/new",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()
        assert result.structuredContent["summary"] == "1 new item."
        assert result.structuredContent["items"][0]["validation"]["ok"] is True
        assert result.content[0].text == "1 new item. Use structuredContent.items."

    @pytest.mark.anyio
    async def test_check_new_empty(self, db):
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}):
            result = await check_new()
        assert result.structuredContent["summary"] == "No new content."
        assert "no new" in result.content[0].text.lower()

    @pytest.mark.anyio
    async def test_check_new_marks_as_read(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Post", "link": "https://example.com/post"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "Post",
            "link": "https://example.com/post",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/post",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            await check_new()
            result = await check_new()
        assert "no new" in result.content[0].text.lower()

    @pytest.mark.anyio
    async def test_check_new_output_format(self, db):
        """Verify the exact output format: - source | title | link"""
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "My Post", "link": "https://example.com/my-post"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "My Post",
            "link": "https://example.com/my-post",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/my-post",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()
        assert result.content[0].text == "1 new item. Use structuredContent.items."

    @pytest.mark.anyio
    async def test_check_new_untitled_items_show_placeholder(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"link": "https://example.com/no-title"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "(untitled)",
            "link": "https://example.com/no-title",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/no-title",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()
        assert result.structuredContent["items"][0]["title"] == "(untitled)"
        assert result.content[0].text == "1 new item. Use structuredContent.items."

    @pytest.mark.anyio
    async def test_check_new_sanitizes_delimiters_in_titles(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "A | B\nC", "link": "https://example.com/post"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "A / B C",
            "link": "https://example.com/post",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/post",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()
        assert result.structuredContent["items"][0]["title"] == "A / B C"
        assert result.content[0].text == "1 new item. Use structuredContent.items."

    @pytest.mark.anyio
    async def test_check_new_exposes_structured_validation_metadata(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Needs Review", "link": "https://example.com/bad"},
        ])
        validation_items = [{
            "source": "Example",
            "source_url": "https://example.com",
            "title": "Needs Review",
            "link": "https://example.com/bad",
            "validation": {
                "checked": True,
                "ok": False,
                "final_url": "https://example.com/moved",
                "status_code": 404,
                "content_type": "text/html",
                "error": "http_404",
            },
        }]
        with patch("src.rss_mcp.server.refresh_sites", new_callable=AsyncMock, return_value={"Example": 1}), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()

        assert result.structuredContent["refresh"] == {"Example": 1}
        item = result.structuredContent["items"][0]
        assert item["validation"]["ok"] is False
        assert item["validation"]["final_url"] == "https://example.com/moved"
        assert result.content[0].text == "1 new item. Use structuredContent.items."
