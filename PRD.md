# PRD: Universal RSS — MCP Feed Tracker

## Overview

A tool that watches any website for new content and exposes it via MCP so an AI agent (Poke) can reliably fetch new links in a single call. Solves the problem of Poke being unreliable at directly scraping sites for new publications.

## Problem

Poke (an AI agent that sends daily morning briefings via iMessage at 7:30am EST) currently tries to check AI research org sites directly for new content. It does a poor job — misses articles, fails to parse pages, and is inconsistent. This tool does the hard work of reliably tracking what's new, so Poke just reads the results.

## How It Works

1. **You give it a URL** (via Poke or directly) — any website
2. **It figures out how to watch it** — checks for RSS/Atom feed first, falls back to scraping the page for new links
3. **It stores what it finds** in a local SQLite database
4. **Poke calls `check_new`** daily and gets a clean chronological list of everything new since last check

## MCP Tools

| Tool | Purpose | When used |
|------|---------|-----------|
| `check_new` | Returns all new links since last check | Daily by Poke for morning briefing |
| `add_site(url, name?)` | Start tracking a site | When you tell Poke "start following X" |
| `remove_site(url)` | Stop tracking a site | When you tell Poke "stop following X" |
| `list_sites` | Show all tracked sites | When you ask Poke "what am I following?" |

## Output Format (check_new)

Chronological list. Each item has three pieces of info:
- **Source** — site/org name
- **Title** — article title (if present)
- **Link** — direct URL

Example:
```
- Anthropic | Agentic coding trends report | https://anthropic.com/research/...
- Google DeepMind | Accelerating mathematical discovery | https://deepmind.google/...
- Andon Labs | Bengt hires a human | https://andonlabs.com/blog/...
```

If nothing is new, returns a clear "no new content" message.

## Site Detection Strategy

When a site is added:
1. Check if the URL itself is an RSS/Atom feed → use directly
2. Check the page for `<link rel="alternate" type="application/rss+xml">` or similar → use that feed
3. No feed found → scrape the page for links, store a snapshot, and diff on future checks

## Scraping Approach

- **Simple HTTP + BeautifulSoup** for static HTML pages (fast, lightweight)
- **Playwright (headless browser)** for JS-heavy sites that render content dynamically
- Auto-detect: try simple HTTP first; if the page has minimal content, retry with Playwright
- Store a "link fingerprint" (set of URLs on the page) per site — new links = items that weren't there before

## Tech Stack

- **Python** with `uv` for dependency management
- **MCP SDK** (`mcp` package) for the MCP server
- **feedparser** for RSS/Atom parsing
- **httpx** + **BeautifulSoup4** for simple scraping
- **Playwright** for JS-heavy sites
- **SQLite** for persistent storage (feeds.db)

## Data Model

**sites** table:
- `id`, `url`, `name`, `feed_url` (null if scraping), `use_playwright` (bool), `last_checked`

**items** table:
- `id`, `site_id`, `title`, `link`, `discovered_at`, `notified` (bool — has check_new returned this yet?)

## Key Behaviors

### What "new" means
- Every item has a `notified` flag. `check_new` returns all items where `notified = false`, then flips them to `true`.
- "New" = anything Poke hasn't seen yet. Works like an inbox that empties when read.
- For RSS feeds: new = any item with a URL not already stored.
- For scraped sites: new = any link on the page that wasn't there last time we checked.

### Adding a new source (intake flow)
1. Fetch the page
2. Look for RSS/Atom `<link>` tag in HTML → if found, use feedparser going forward
3. No feed found → scrape all links on the page and store as **baseline** (marked as already notified)
4. On future checks, links not in baseline = new items
5. This prevents flooding with old links when you first add a site

### Other behaviors
- Feed items are deduped by URL
- Sites with no new content are silently skipped (no noise)

## Future Considerations (not building now)

- Scheduled auto-refresh (cron or background process) so data is fresh before Poke's 7:30am call
- Tier system (priority vs. check-if-time) to match current briefing structure
- Per-site scraping rules for tricky pages
