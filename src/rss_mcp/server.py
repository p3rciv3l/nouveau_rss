from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from .fetcher import detect_feed_url, parse_rss_items, scrape_links
from .storage import Storage

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
        site = db.add_site(url, name)
        items = scrape_links(url, html)
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


@mcp.tool()
async def check_new() -> str:
    """Get all new links since last check. Returns source, title, and link for each new item."""
    db = get_storage()
    items = db.get_new_items()
    if not items:
        return "No new content."
    lines = []
    for item in items:
        title = item["title"] or "(untitled)"
        lines.append(f"- {item['source']} | {title} | {item['link']}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
