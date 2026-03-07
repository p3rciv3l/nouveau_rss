import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from src.rss_mcp.server import add_site, check_new, refresh_sites
from src.rss_mcp.storage import Storage


@pytest.fixture
def db(tmp_path):
    storage = Storage(tmp_path / "test.db")
    with patch("src.rss_mcp.server.get_storage", return_value=storage):
        yield storage


class TestPlaywrightFallback:
    @pytest.mark.anyio
    async def test_falls_back_to_playwright_when_http_returns_sparse_content(self, db):
        """If simple HTTP gets a near-empty page, retry with Playwright."""
        sparse_html = "<html><body><div id='app'></div></body></html>"
        rich_html = """
        <html><body>
            <a href="https://example.com/post-1">Post 1</a>
            <a href="https://example.com/post-2">Post 2</a>
            <a href="https://example.com/post-3">Post 3</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=sparse_html), \
             patch("src.rss_mcp.server.fetch_page_playwright", new_callable=AsyncMock, return_value=rich_html):
            result = await add_site("https://example.com", "Example")

        assert "Example" in result
        sites = db.list_sites()
        assert sites[0]["use_playwright"] == 1

    @pytest.mark.anyio
    async def test_no_fallback_when_http_has_enough_links(self, db):
        """If simple HTTP gets enough content, don't bother with Playwright."""
        good_html = """
        <html><body>
            <a href="https://example.com/post-1">Post 1</a>
            <a href="https://example.com/post-2">Post 2</a>
            <a href="https://example.com/post-3">Post 3</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=good_html), \
             patch("src.rss_mcp.server.fetch_page_playwright", new_callable=AsyncMock) as mock_pw:
            await add_site("https://example.com", "Example")

        mock_pw.assert_not_called()
        sites = db.list_sites()
        assert sites[0]["use_playwright"] == 0


class TestPlaywrightRefresh:
    @pytest.mark.anyio
    async def test_refresh_uses_playwright_for_flagged_sites(self, db):
        """Sites marked use_playwright should be fetched with Playwright during refresh."""
        site = db.add_site("https://example.com", "Example")
        db.set_use_playwright(site["id"], True)
        db.add_items(site["id"], [
            {"title": "Old", "link": "https://example.com/old"},
        ], baseline=True)

        new_html = """
        <html><body>
            <a href="https://example.com/old">Old</a>
            <a href="https://example.com/new">New Post</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page_playwright", new_callable=AsyncMock, return_value=new_html) as mock_pw, \
             patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock) as mock_http:
            results = await refresh_sites()

        mock_pw.assert_called_once_with("https://example.com")
        mock_http.assert_not_called()
        assert results["Example"] == 1

    @pytest.mark.anyio
    async def test_refresh_uses_http_for_normal_sites(self, db):
        """Sites without use_playwright flag should use normal HTTP."""
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Old", "link": "https://example.com/old"},
        ], baseline=True)

        new_html = """
        <html><body>
            <a href="https://example.com/old">Old</a>
            <a href="https://example.com/new">New</a>
        </body></html>
        """
        with patch("src.rss_mcp.server.fetch_page", new_callable=AsyncMock, return_value=new_html) as mock_http, \
             patch("src.rss_mcp.server.fetch_page_playwright", new_callable=AsyncMock) as mock_pw:
            results = await refresh_sites()

        mock_http.assert_called_once()
        mock_pw.assert_not_called()


class TestPlaywrightFetchPage:
    @pytest.mark.anyio
    async def test_fetch_page_playwright_returns_html(self):
        """Verify fetch_page_playwright calls Playwright and returns page content."""
        from src.rss_mcp.server import fetch_page_playwright

        mock_page = AsyncMock()
        mock_page.content = AsyncMock(return_value="<html><body>rendered</body></html>")
        mock_page.goto = AsyncMock()
        mock_page.wait_for_load_state = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_browser.close = AsyncMock()

        mock_pw_instance = AsyncMock()
        mock_pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_cm = AsyncMock()
        mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("src.rss_mcp.server.async_playwright", return_value=mock_pw_cm):
            result = await fetch_page_playwright("https://example.com")

        assert result == "<html><body>rendered</body></html>"
        mock_page.goto.assert_called_once_with("https://example.com")
