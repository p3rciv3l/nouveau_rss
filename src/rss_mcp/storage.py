import sqlite3
from pathlib import Path


class Storage:
    def __init__(self, db_path: str | Path):
        self._db = sqlite3.connect(str(db_path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                feed_url TEXT,
                use_playwright INTEGER NOT NULL DEFAULT 0,
                last_checked REAL
            );
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                title TEXT,
                link TEXT UNIQUE NOT NULL,
                discovered_at REAL NOT NULL DEFAULT (unixepoch('now')),
                notified INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );
        """)

    def add_site(self, url: str, name: str | None = None, feed_url: str | None = None) -> dict:
        name = name or url
        try:
            cursor = self._db.execute(
                "INSERT INTO sites (url, name, feed_url) VALUES (?, ?, ?)",
                (url, name, feed_url),
            )
            self._db.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Site already tracked: {url}")
        return {"id": cursor.lastrowid, "url": url, "name": name, "feed_url": feed_url}

    def remove_site(self, url: str):
        row = self._db.execute("SELECT id FROM sites WHERE url=?", (url,)).fetchone()
        if not row:
            raise ValueError(f"Site not found: {url}")
        self._db.execute("DELETE FROM items WHERE site_id=?", (row["id"],))
        self._db.execute("DELETE FROM sites WHERE id=?", (row["id"],))
        self._db.commit()

    def list_sites(self) -> list[dict]:
        rows = self._db.execute("SELECT id, url, name, feed_url, last_checked FROM sites ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def add_items(self, site_id: int, items: list[dict], baseline: bool = False) -> int:
        count = 0
        for item in items:
            link = item.get("link")
            if not link:
                continue
            title = item.get("title")
            notified = 1 if baseline else 0
            try:
                self._db.execute(
                    "INSERT INTO items (site_id, title, link, notified) VALUES (?, ?, ?, ?)",
                    (site_id, title, link, notified),
                )
                count += 1
            except sqlite3.IntegrityError:
                pass  # duplicate link
        self._db.commit()
        return count

    def get_new_items(self) -> list[dict]:
        rows = self._db.execute("""
            SELECT i.id, i.title, i.link, i.discovered_at, s.name as source
            FROM items i
            JOIN sites s ON i.site_id = s.id
            WHERE i.notified = 0
            ORDER BY i.id ASC
        """).fetchall()
        items = [dict(r) for r in rows]
        if items:
            ids = [i["id"] for i in items]
            self._db.execute(
                f"UPDATE items SET notified=1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            self._db.commit()
        return items
