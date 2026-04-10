import re
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup


_ALLOWED_SCHEMES = {"http", "https"}
_FEED_MIME_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+xml",
    "text/rss+xml",
    "text/atom+xml",
}
_GENERIC_PATHS = {
    "about",
    "archive",
    "author",
    "authors",
    "category",
    "categories",
    "comments",
    "contact",
    "blog",
    "feed",
    "home",
    "index",
    "login",
    "newsletter",
    "news",
    "privacy",
    "register",
    "research",
    "rss",
    "search",
    "share",
    "sign-in",
    "signin",
    "signup",
    "sitemap",
    "subscribe",
    "posts",
    "articles",
    "tag",
    "tags",
    "terms",
    "atom",
}
_GENERIC_LINK_TEXT = {
    "about",
    "archive",
    "back",
    "blog",
    "comment",
    "comments",
    "contact",
    "feed",
    "home",
    "index",
    "login",
    "more",
    "next",
    "newsletter",
    "page 1",
    "privacy",
    "previous",
    "read more",
    "register",
    "rss",
    "search",
    "share",
    "sign in",
    "sign up",
    "sitemap",
    "subscribe",
    "tag",
    "tags",
    "terms",
}
_GENERIC_PATH_PREFIXES = (
    "tag/",
    "tags/",
    "category/",
    "categories/",
    "author/",
    "authors/",
    "page/",
    "search/",
    "feed/",
    "rss/",
    "atom/",
    "subscribe/",
    "newsletter/",
    "login/",
    "signin/",
    "sign-in/",
    "signup/",
    "register/",
    "privacy/",
    "terms/",
    "comments/",
    "share/",
)
_IGNORED_EXTENSIONS = {
    ".7z",
    ".avi",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".json",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".rss",
    ".svg",
    ".tar",
    ".tgz",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}


def _normalize_url(raw_url: str, base_url: str | None = None) -> str | None:
    candidate = (raw_url or "").strip()
    if not candidate:
        return None

    if " " in candidate:
        candidate = candidate.replace(" ", "%20")

    if base_url:
        candidate = urljoin(base_url, candidate)

    candidate, _ = urldefrag(candidate)
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES or not parts.netloc:
        return None

    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _looks_like_feed_document(html: str) -> bool:
    snippet = html.lstrip().lower()[:4096]
    return bool(re.search(r"<\s*(rss|feed|rdf:rdf)\b", snippet))


def _rel_values(tag) -> set[str]:
    rel = tag.get("rel") or []
    if isinstance(rel, str):
        return {rel.lower()}
    return {str(value).lower() for value in rel if value}


def _looks_like_content_link(url: str, title: str = "") -> bool:
    parts = urlsplit(url)
    path = parts.path.strip("/").lower()
    if not path:
        return False

    if any(path.endswith(extension) for extension in _IGNORED_EXTENSIONS):
        return False

    if path in _GENERIC_PATHS:
        return False

    if path.startswith(_GENERIC_PATH_PREFIXES):
        return False

    normalized_title = re.sub(r"\s+", " ", title).strip().lower()
    if normalized_title in _GENERIC_LINK_TEXT and "/" not in path:
        return False

    return True


def _extract_entry_link(entry, feed_base: str | None) -> str | None:
    candidate = entry.get("link") or entry.get("href")
    normalized = _normalize_url(candidate, feed_base)
    if normalized:
        return normalized

    for link in entry.get("links") or []:
        if not isinstance(link, dict):
            continue
        href = link.get("href") or link.get("link")
        normalized = _normalize_url(href, feed_base)
        if not normalized:
            continue

        rel = str(link.get("rel") or "").lower()
        link_type = str(link.get("type") or "").lower()
        if rel in {"alternate", "self", ""} or "html" in link_type or "xml" in link_type:
            return normalized

    return None


def detect_feed_url(base_url: str, html: str) -> str | None:
    """Look for RSS/Atom feed link tags in HTML. Returns absolute feed URL or None."""
    normalized_base = _normalize_url(base_url)
    if normalized_base and _looks_like_feed_document(html):
        return normalized_base

    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link"):
        if "alternate" not in _rel_values(link):
            continue

        href = _normalize_url(link.get("href", ""), base_url)
        if not href:
            continue

        link_type = (link.get("type") or "").lower().split(";", 1)[0].strip()
        if link_type in _FEED_MIME_TYPES:
            return href
        if "xml" in link_type and any(token in href for token in ("/feed", "/rss", "/atom")):
            return href
        if not link_type and any(token in href for token in ("/feed", "/rss", "/atom")):
            return href
    return None


def parse_rss_items(xml: str, source_url: str | None = None) -> list[dict]:
    """Parse RSS/Atom XML and return a list of {title, link} dicts."""
    parsed = feedparser.parse(xml)
    feed_base = parsed.feed.get("link") if getattr(parsed, "feed", None) else None
    link_base = feed_base or source_url
    items = []
    for entry in parsed.entries:
        link = _extract_entry_link(entry, link_base)
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
    normalized_base = _normalize_url(base_url)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("#", "mailto:", "javascript:", "tel:", "sms:")):
            continue

        url = _normalize_url(href, base_url)
        if not url:
            continue

        if not _looks_like_content_link(url, a.get_text(" ", strip=True) or ""):
            continue

        if normalized_base and url.rstrip("/") == normalized_base.rstrip("/"):
            continue

        if url in seen:
            continue
        seen.add(url)
        title = a.get_text(strip=True) or ""
        items.append({"title": title, "link": url})
    return items
