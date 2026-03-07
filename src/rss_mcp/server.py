import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .fetcher import detect_feed_url, parse_rss_items, scrape_links
from .storage import Storage

MIN_LINKS_FOR_HTTP = 3

DB_PATH = Path(__file__).parent.parent.parent / "feeds.db"

mcp = FastMCP("nouveau-rss", instructions="""
RSS feed tracker. Use check_new to get new links from tracked sites.
Use add_site/remove_site/list_sites to manage which sites are tracked.
""")

_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(DB_PATH)
    return _storage


async def fetch_page(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def fetch_feed_xml(feed_url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(feed_url)
        resp.raise_for_status()
        return resp.text


async def fetch_page_playwright(url: str) -> str:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
        html = await page.content()
        await context.close()
        await browser.close()
        return html


@mcp.tool()
async def add_site(url: str, name: str | None = None) -> str:
    """Start tracking a website for new links. Give it any URL — it will auto-detect RSS or scrape the page."""
    db = get_storage()
    name = name or url

    # Check for duplicate
    existing = [s for s in db.list_sites() if s["url"] == url]
    if existing:
        return f"Already tracking {url}"

    try:
        html = await fetch_page(url)
    except Exception as e:
        return f"Failed to fetch {url}: {e}"

    # Try to detect RSS/Atom feed
    feed_url = detect_feed_url(url, html)

    if feed_url:
        site = db.add_site(url, name, feed_url=feed_url)
        try:
            xml = await fetch_feed_xml(feed_url)
            items = parse_rss_items(xml)
            count = db.add_items(site["id"], items, baseline=True)
        except Exception:
            count = 0
        return f"Now tracking '{name}' via RSS feed ({count} existing items catalogued)"
    else:
        items = scrape_links(url, html)
        use_pw = False
        if len(items) < MIN_LINKS_FOR_HTTP:
            try:
                html = await fetch_page_playwright(url)
                pw_items = scrape_links(url, html)
                if len(pw_items) > len(items):
                    items = pw_items
                    use_pw = True
            except Exception:
                pass  # stick with HTTP results
        site = db.add_site(url, name)
        if use_pw:
            db.set_use_playwright(site["id"], True)
        count = db.add_items(site["id"], items, baseline=True)
        return f"Now tracking '{name}' via page scraping ({count} existing links catalogued)"


@mcp.tool()
async def remove_site(url: str) -> str:
    """Stop tracking a website."""
    db = get_storage()
    try:
        db.remove_site(url)
        return f"Removed {url}"
    except ValueError:
        return f"Site not found: {url}"


@mcp.tool()
async def list_sites() -> str:
    """List all tracked sites."""
    db = get_storage()
    sites = db.list_sites()
    if not sites:
        return "No sites being tracked."
    lines = []
    for s in sites:
        method = "RSS" if s["feed_url"] else "scraping"
        lines.append(f"- {s['name']} ({method})\n  {s['url']}")
    return "\n".join(lines)


async def refresh_sites() -> dict[str, int | str]:
    """Refresh all tracked sites. Returns {site_name: new_item_count or 'error'}."""
    db = get_storage()
    sites = db.list_sites()
    results = {}
    for site in sites:
        try:
            if site["feed_url"]:
                xml = await fetch_feed_xml(site["feed_url"])
                items = parse_rss_items(xml)
            elif site.get("use_playwright"):
                html = await fetch_page_playwright(site["url"])
                items = scrape_links(site["url"], html)
            else:
                html = await fetch_page(site["url"])
                items = scrape_links(site["url"], html)
            count = db.add_items(site["id"], items)
            results[site["name"]] = count
        except Exception:
            results[site["name"]] = "error"
    return results


@mcp.tool()
async def check_new() -> str:
    """Get all new links since last check. Refreshes all sites first, then returns new items."""
    await refresh_sites()
    db = get_storage()
    items = db.get_new_items()
    if not items:
        return "No new content."
    lines = []
    for item in items:
        title = item["title"] or "(untitled)"
        lines.append(f"- {item['source']} | {title} | {item['link']}")
    return "\n".join(lines)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {self.api_key}":
            return await call_next(request)
        return JSONResponse({"error": "unauthorized"}, status_code=401)


if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        port = 8000
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])

        api_key = os.environ.get("NOUVEAU_RSS_API_KEY")
        if not api_key:
            print("ERROR: Set NOUVEAU_RSS_API_KEY env var to secure the server.")
            print("  export NOUVEAU_RSS_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')")
            sys.exit(1)

        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port

        # Wrap the MCP app with API key auth (SSE for Poke compatibility)
        app = mcp.sse_app()
        app.add_middleware(ApiKeyMiddleware, api_key=api_key)

        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
    else:
        mcp.run()
