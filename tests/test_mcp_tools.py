import pytest
from unittest.mock import patch, AsyncMock, MagicMock
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
    async def test_add_site_without_rss_scrapes_baseline(self, db):
        """When no RSS, scrape links as baseline (marked notified)."""
        fake_html = """
        <html><body>
            <a href="https://example.com/post-1">Post 1</a>
            <a href="https://example.com/post-2">Post 2</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=fake_html):
            result = await add_site("https://example.com", "Example")

        assert "Example" in result
        # Baseline items should NOT show up as new
        items = db.get_new_items()
        assert items == []

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


class TestCheckNew:
    @pytest.mark.anyio
    async def test_check_new_returns_new_items(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "New Post", "link": "https://example.com/new"},
        ])
        result = await check_new()
        assert "New Post" in result
        assert "https://example.com/new" in result
        assert "Example" in result

    @pytest.mark.anyio
    async def test_check_new_empty(self, db):
        result = await check_new()
        assert "no new" in result.lower()

    @pytest.mark.anyio
    async def test_check_new_marks_as_read(self, db):
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Post", "link": "https://example.com/post"},
        ])
        await check_new()
        result = await check_new()
        assert "no new" in result.lower()
