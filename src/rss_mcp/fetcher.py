from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup


def detect_feed_url(base_url: str, html: str) -> str | None:
    """Look for RSS/Atom feed link tags in HTML. Returns absolute feed URL or None."""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", rel="alternate"):
        link_type = (link.get("type") or "").lower()
        if link_type in ("application/rss+xml", "application/atom+xml"):
            href = link.get("href", "")
            if href:
                return urljoin(base_url, href)
    return None


def parse_rss_items(xml: str) -> list[dict]:
    """Parse RSS/Atom XML and return a list of {title, link} dicts."""
    parsed = feedparser.parse(xml)
    items = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue
        items.append({
            "title": entry.get("title", ""),
            "link": link,
        })
    return items


def scrape_links(base_url: str, html: str) -> list[dict]:
    """Extract meaningful links from an HTML page. Returns list of {title, link} dicts."""
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Skip fragments, mailto, javascript
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        # Skip if just the base domain with no path
        if url.rstrip("/") == base_url.rstrip("/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True) or ""
        items.append({"title": title, "link": url})
    return items
