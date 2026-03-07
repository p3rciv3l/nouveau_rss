#!/bin/sh

# Seed sites on first run (if DB doesn't exist yet)
if [ ! -f /app/feeds.db ]; then
    echo "First run — seeding sites..."
    uv run python seed_sites.py || echo "Seeding had errors (some sites may have failed)"
fi

# Start the MCP server on HTTP
exec uv run python -m src.rss_mcp.server --http --port ${PORT:-8000}
