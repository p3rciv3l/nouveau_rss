import asyncio
import os
import re
from pathlib import Path
from html import unescape
from typing import Annotated, Any, TypedDict
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from playwright.async_api import async_playwright
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .fetcher import detect_feed_url, parse_rss_items, scrape_links
from .storage import Storage

MIN_LINKS_FOR_HTTP = 3
ALLOWED_URL_SCHEMES = {"http", "https"}
BOILERPLATE_PATH_SEGMENTS = {
    "about",
    "archive",
    "archives",
    "author",
    "authors",
    "category",
    "categories",
    "comments",
    "contact",
    "feed",
    "help",
    "login",
    "logout",
    "newsletter",
    "page",
    "privacy",
    "register",
    "rss",
    "search",
    "share",
    "signin",
    "sign-in",
    "signup",
    "sign-up",
    "sitemap",
    "subscribe",
    "tag",
    "tags",
    "terms",
}
WHITESPACE_RE = re.compile(r"\s+")
LINK_VALIDATION_CONCURRENCY = 5
VALIDATION_TIMEOUT_SECONDS = 15
VALIDATION_USER_AGENT = "nouveau-rss/0.2"


class LinkValidation(TypedDict):
    checked: bool
    ok: bool
    final_url: str | None
    status_code: int | None
    content_type: str | None
    error: str | None


class CheckNewItem(TypedDict):
    source: str
    source_url: str
    title: str
    link: str
    validation: LinkValidation


class CheckNewPayload(TypedDict):
    summary: str
    refresh: dict[str, int | str]
    items: list[CheckNewItem]

DB_PATH = Path(__file__).parent.parent.parent / "feeds.db"

mcp = FastMCP("nouveau-rss", instructions="""
RSS feed tracker. Use check_new to get new links from tracked sites.
Use add_site/remove_site/list_sites to manage which sites are tracked.
""", transport_security=TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
))

_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(DB_PATH)
    return _storage


def _clean_text(value: str | None) -> str:
    text = unescape((value or "").replace("\x00", " "))
    return WHITESPACE_RE.sub(" ", text).strip()


def _format_output_field(value: str | None) -> str:
    return _clean_text(value).replace("|", "/")


def _normalize_url(raw_url: str | None, base_url: str) -> str | None:
    if not raw_url:
        return None

    candidate = raw_url.strip()
    if not candidate:
        return None

    candidate = urljoin(base_url, candidate)
    candidate, _ = urldefrag(candidate)

    parsed = urlparse(candidate)
    if parsed.scheme not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        return None

    return parsed.geturl()


def _is_boilerplate_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.strip("/").lower()
    if not path:
        return True

    segments = {segment for segment in path.split("/") if segment}
    return bool(segments & BOILERPLATE_PATH_SEGMENTS)


def _prepare_items(base_url: str, items: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    seen: set[str] = set()
    for item in items:
        link = _normalize_url(item.get("link"), base_url)
        if not link or link in seen or _is_boilerplate_url(link):
            continue
        seen.add(link)
        prepared.append({
            "title": _clean_text(item.get("title")),
            "link": link,
        })
    return prepared


def _looks_like_feed_document(body: str) -> bool:
    prefix = body.lstrip()[:64].lower()
    return prefix.startswith("<?xml") or prefix.startswith("<rss") or prefix.startswith("<feed") or prefix.startswith("<rdf:rdf")


def _parse_feed_items(feed_url: str, xml: str) -> list[dict]:
    return _prepare_items(feed_url, parse_rss_items(xml, source_url=feed_url))


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


async def _validate_link(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> CheckNewItem:
    async with semaphore:
        source = _format_output_field(item["source"]) or "(unknown source)"
        title = _format_output_field(item["title"]) or "(untitled)"
        link = _format_output_field(item["link"])
        source_url = _format_output_field(item.get("source_url")) or ""

        validation: LinkValidation = {
            "checked": True,
            "ok": False,
            "final_url": None,
            "status_code": None,
            "content_type": None,
            "error": None,
        }

        try:
            response = await client.get(link)
            validation["status_code"] = response.status_code
            validation["final_url"] = str(response.url)
            validation["content_type"] = response.headers.get("content-type")
            validation["ok"] = response.is_success
            if not response.is_success:
                validation["error"] = f"http_{response.status_code}"
        except Exception as exc:
            validation["error"] = str(exc)

        return {
            "source": source,
            "source_url": source_url,
            "title": title,
            "link": link,
            "validation": validation,
        }


async def _validate_new_items(items: list[dict[str, Any]]) -> list[CheckNewItem]:
    if not items:
        return []

    timeout = httpx.Timeout(VALIDATION_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(LINK_VALIDATION_CONCURRENCY)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": VALIDATION_USER_AGENT},
    ) as client:
        tasks = [_validate_link(client, item, semaphore) for item in items]
        return await asyncio.gather(*tasks)


@mcp.tool()
async def add_site(url: str, name: str | None = None) -> str:
    """Start tracking a website for new links. Give it any URL — it will auto-detect RSS or scrape the page."""
    db = get_storage()
    name = _clean_text(name) or url

    # Check for duplicate
    existing = [s for s in db.list_sites() if s["url"] == url]
    if existing:
        return f"Already tracking {url}"

    try:
        html = await fetch_page(url)
    except Exception as e:
        return f"Failed to fetch {url}: {e}"

    feed_url = detect_feed_url(url, html)
    is_direct_feed = _looks_like_feed_document(html)

    if feed_url or is_direct_feed:
        resolved_feed_url = feed_url or url
        site = db.add_site(url, name, feed_url=resolved_feed_url)
        try:
            xml = html if is_direct_feed else await fetch_feed_xml(resolved_feed_url)
            items = _parse_feed_items(resolved_feed_url, xml)
            count = db.add_items(site["id"], items, baseline=True)
        except Exception:
            count = 0
        return f"Now tracking '{name}' via RSS feed ({count} existing items catalogued)"
    else:
        items = _prepare_items(url, scrape_links(url, html))
        use_pw = False
        if len(items) < MIN_LINKS_FOR_HTTP:
            try:
                html = await fetch_page_playwright(url)
                pw_items = _prepare_items(url, scrape_links(url, html))
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
        lines.append(f"- {_format_output_field(s['name'])} ({method})\n  {_format_output_field(s['url'])}")
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
                items = _parse_feed_items(site["feed_url"], xml)
            elif site.get("use_playwright"):
                html = await fetch_page_playwright(site["url"])
                if _looks_like_feed_document(html):
                    items = _parse_feed_items(site["url"], html)
                else:
                    items = _prepare_items(site["url"], scrape_links(site["url"], html))
            else:
                html = await fetch_page(site["url"])
                if _looks_like_feed_document(html):
                    items = _parse_feed_items(site["url"], html)
                else:
                    items = _prepare_items(site["url"], scrape_links(site["url"], html))
            count = db.add_items(site["id"], items)
            results[site["name"]] = count
        except Exception:
            results[site["name"]] = "error"
    return results


@mcp.tool()
async def check_new() -> Annotated[CallToolResult, CheckNewPayload]:
    """Get all new links since last check. Refreshes all sites first, then returns new items."""
    refresh = await refresh_sites()
    db = get_storage()
    items = db.get_new_items()
    if not items:
        payload: CheckNewPayload = {
            "summary": "No new content.",
            "refresh": refresh,
            "items": [],
        }
        return CallToolResult(
            content=[TextContent(type="text", text="No new content.")],
            structuredContent=payload,
            isError=False,
        )

    validated_items = await _validate_new_items(items)
    summary = f"{len(validated_items)} new item{'s' if len(validated_items) != 1 else ''}."
    payload = {
        "summary": summary,
        "refresh": refresh,
        "items": validated_items,
    }
    return CallToolResult(
        content=[TextContent(type="text", text=f"{summary} Use structuredContent.items.")],
        structuredContent=payload,
        isError=False,
    )


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
