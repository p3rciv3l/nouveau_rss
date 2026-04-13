import pytest
from unittest.mock import patch, AsyncMock
from src.rss_mcp.server import check_new, refresh_sites
from src.rss_mcp.storage import Storage


@pytest.fixture
def db(tmp_path):
    storage = Storage(tmp_path / "test.db")
    with patch("src.rss_mcp.server.get_storage", return_value=storage):
        yield storage


class TestRefreshRssSites:
    @pytest.mark.anyio
    async def test_fetches_new_rss_items(self, db):
        """Refresh picks up new items from an RSS feed."""
        db.add_site("https://example.com", "Example", feed_url="https://example.com/feed.xml")
        # Simulate one existing item (baseline)
        site = db.list_sites()[0]
        db.add_items(site["id"], [
            {"title": "Old Post", "link": "https://example.com/old"},
        ], baseline=True)

        fake_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
            <item><title>Old Post</title><link>https://example.com/old</link></item>
            <item><title>New Post</title><link>https://example.com/new</link></item>
        </channel></rss>
        """
        with patch("src.rss_mcp.server.fetch_feed_xml", new_callable=AsyncMock, return_value=fake_rss):
            results = await refresh_sites()

        assert results["Example"] == 1  # only the new post
        assert db.list_sites()[0]["last_checked"] is not None
        assert db.list_sites()[0]["last_error"] is None

    @pytest.mark.anyio
    async def test_rss_feed_error_doesnt_crash(self, db):
        """If a feed fails to fetch, refresh continues with other sites."""
        db.add_site("https://broken.com", "Broken", feed_url="https://broken.com/feed.xml")
        db.add_site("https://working.com", "Working", feed_url="https://working.com/feed.xml")

        good_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
            <item><title>Post</title><link>https://working.com/post</link></item>
        </channel></rss>
        """

        async def mock_fetch(url):
            if "broken" in url:
                raise httpx.HTTPError("connection failed")
            return good_rss

        import httpx
        with patch("src.rss_mcp.server.fetch_feed_xml", new_callable=AsyncMock, side_effect=mock_fetch):
            results = await refresh_sites()

        assert results["Broken"] == "error"
        assert results["Working"] == 1
        sites = {site["name"]: site for site in db.list_sites()}
        assert sites["Broken"]["last_error"] is not None
        assert sites["Working"]["last_error"] is None


class TestRefreshScrapedSites:
    @pytest.mark.anyio
    async def test_finds_new_links_on_page(self, db):
        """Refresh detects new links that weren't there before."""
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Old", "link": "https://example.com/old"},
        ], baseline=True)

        new_html = """
        <html><body>
            <a href="https://example.com/old">Old</a>
            <a href="https://example.com/new">New Post</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=new_html):
            results = await refresh_sites()

        assert results["Example"] == 1

    @pytest.mark.anyio
    async def test_scrape_error_doesnt_crash(self, db):
        """If a page fails to fetch, refresh continues."""
        db.add_site("https://broken.com", "Broken")

        import httpx
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock,
                   side_effect=httpx.HTTPError("timeout")):
            results = await refresh_sites()

        assert results["Broken"] == "error"
        assert db.list_sites()[0]["last_error"] is not None


class TestCheckNewWithRefresh:
    @pytest.mark.anyio
    async def test_check_new_refreshes_first(self, db):
        """check_new should refresh all sites before returning new items."""
        db.add_site("https://example.com", "Example", feed_url="https://example.com/feed.xml")

        fake_rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
            <item><title>Fresh Post</title><link>https://example.com/fresh</link></item>
        </channel></rss>
        """
        validation_items = [{
            "id": 1,
            "source": "Example",
            "source_url": "https://example.com",
            "title": "Fresh Post",
            "link": "https://example.com/fresh",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/fresh",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.fetch_feed_xml", new_callable=AsyncMock, return_value=fake_rss), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()

        assert result.content[0].text == (
            "1 new item. Use structuredContent.items, then call acknowledge_items with their ids after delivery."
        )
        assert result.structuredContent["items"][0]["title"] == "Fresh Post"
        assert result.structuredContent["items"][0]["link"] == "https://example.com/fresh"
        assert result.structuredContent["items"][0]["id"] is not None

    @pytest.mark.anyio
    async def test_check_new_still_works_when_refresh_fails(self, db):
        """If refresh fails entirely, check_new still returns any previously stored new items."""
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Stored Post", "link": "https://example.com/stored"},
        ])

        import httpx
        validation_items = [{
            "id": 1,
            "source": "Example",
            "source_url": "https://example.com",
            "title": "Stored Post",
            "link": "https://example.com/stored",
            "validation": {
                "checked": True,
                "ok": True,
                "final_url": "https://example.com/stored",
                "status_code": 200,
                "content_type": "text/html",
                "error": None,
            },
        }]
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock,
                   side_effect=httpx.HTTPError("fail")), \
             patch("src.rss_mcp.server._validate_new_items", new_callable=AsyncMock, return_value=validation_items):
            result = await check_new()

        assert result.content[0].text == (
            "1 new item. Use structuredContent.items, then call acknowledge_items with their ids after delivery."
        )
        assert result.structuredContent["items"][0]["title"] == "Stored Post"
