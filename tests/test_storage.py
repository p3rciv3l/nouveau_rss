import pytest
from src.rss_mcp.storage import Storage


@pytest.fixture
def db(tmp_path):
    """Fresh in-memory-like DB for each test."""
    return Storage(tmp_path / "test.db")


class TestAddSite:
    def test_add_site_stores_url_and_name(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        assert site["url"] == "https://anthropic.com/research"
        assert site["name"] == "Anthropic"
        assert site["id"] is not None

    def test_add_site_without_name_uses_url(self, db):
        site = db.add_site("https://anthropic.com/research")
        assert site["name"] == "https://anthropic.com/research"

    def test_add_duplicate_url_raises(self, db):
        db.add_site("https://anthropic.com/research", "Anthropic")
        with pytest.raises(ValueError, match="already tracked"):
            db.add_site("https://anthropic.com/research", "Anthropic Again")

    def test_add_site_with_feed_url(self, db):
        site = db.add_site(
            "https://anthropic.com/research",
            "Anthropic",
            feed_url="https://anthropic.com/feed.xml",
        )
        assert site["feed_url"] == "https://anthropic.com/feed.xml"


class TestRemoveSite:
    def test_remove_existing_site(self, db):
        db.add_site("https://anthropic.com/research", "Anthropic")
        db.remove_site("https://anthropic.com/research")
        assert db.list_sites() == []

    def test_remove_site_also_removes_items(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])
        db.remove_site("https://anthropic.com/research")
        assert db.get_new_items() == []

    def test_remove_nonexistent_site_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.remove_site("https://nonexistent.com")

    def test_site_item_foreign_key_cascades(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])

        db._db.execute("DELETE FROM sites WHERE id=?", (site["id"],))
        db._db.commit()

        assert db.get_new_items() == []


class TestListSites:
    def test_list_empty(self, db):
        assert db.list_sites() == []

    def test_list_multiple_sites(self, db):
        db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_site("https://deepmind.google/research", "DeepMind")
        sites = db.list_sites()
        assert len(sites) == 2
        names = {s["name"] for s in sites}
        assert names == {"Anthropic", "DeepMind"}


class TestSetUsePlaywright:
    def test_set_use_playwright(self, db):
        site = db.add_site("https://example.com", "Example")
        db.set_use_playwright(site["id"], True)
        sites = db.list_sites()
        assert sites[0]["use_playwright"] == 1

    def test_unset_use_playwright(self, db):
        site = db.add_site("https://example.com", "Example")
        db.set_use_playwright(site["id"], True)
        db.set_use_playwright(site["id"], False)
        sites = db.list_sites()
        assert sites[0]["use_playwright"] == 0

    def test_set_use_playwright_missing_site_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            db.set_use_playwright(9999, True)


class TestListSitesFields:
    def test_list_sites_includes_use_playwright(self, db):
        db.add_site("https://example.com", "Example")
        sites = db.list_sites()
        assert "use_playwright" in sites[0]

    def test_list_sites_includes_feed_url(self, db):
        db.add_site("https://example.com", "Example", feed_url="https://example.com/feed")
        sites = db.list_sites()
        assert sites[0]["feed_url"] == "https://example.com/feed"


class TestSchema:
    def test_items_indexes_exist(self, db):
        rows = db._db.execute("PRAGMA index_list('items')").fetchall()
        index_names = {row["name"] for row in rows}
        assert {"idx_items_notified_id", "idx_items_site_id"}.issubset(index_names)


class TestAddItems:
    def test_add_items_to_site(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        count = db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
            {"title": "Post 2", "link": "https://anthropic.com/post-2"},
        ])
        assert count == 2

    def test_duplicate_links_are_skipped(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])
        count = db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
            {"title": "Post 2", "link": "https://anthropic.com/post-2"},
        ])
        assert count == 1  # only Post 2 is new

    def test_items_without_title(self, db):
        site = db.add_site("https://example.com", "Example")
        count = db.add_items(site["id"], [
            {"link": "https://example.com/page"},
        ])
        assert count == 1

    def test_items_without_link_are_skipped(self, db):
        site = db.add_site("https://example.com", "Example")
        count = db.add_items(site["id"], [
            {"title": "No link"},
            {},
        ])
        assert count == 0

    def test_items_with_empty_link_are_skipped(self, db):
        site = db.add_site("https://example.com", "Example")
        count = db.add_items(site["id"], [
            {"title": "Empty link", "link": ""},
        ])
        assert count == 0


class TestGetNewItems:
    def test_new_items_returned_chronologically(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
            {"title": "Post 2", "link": "https://anthropic.com/post-2"},
        ])
        items = db.get_new_items()
        assert len(items) == 2
        assert items[0]["title"] == "Post 1"
        assert items[1]["title"] == "Post 2"

    def test_new_items_include_source_name(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])
        items = db.get_new_items()
        assert items[0]["source"] == "Anthropic"

    def test_get_new_items_does_not_mark_as_notified(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])
        first_items = db.get_new_items()
        second_items = db.get_new_items()
        assert len(first_items) == 1
        assert len(second_items) == 1

    def test_mark_items_notified_marks_only_selected_items(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
            {"title": "Post 2", "link": "https://anthropic.com/post-2"},
        ])
        items = db.get_new_items()
        marked = db.mark_items_notified([items[0]["id"]])
        remaining = db.get_new_items()

        assert marked == 1
        assert len(remaining) == 1
        assert remaining[0]["title"] == "Post 2"

    def test_items_across_multiple_sites(self, db):
        s1 = db.add_site("https://anthropic.com/research", "Anthropic")
        s2 = db.add_site("https://deepmind.google/research", "DeepMind")
        db.add_items(s1["id"], [
            {"title": "A Post", "link": "https://anthropic.com/a"},
        ])
        db.add_items(s2["id"], [
            {"title": "D Post", "link": "https://deepmind.google/d"},
        ])
        items = db.get_new_items()
        assert len(items) == 2
        sources = {i["source"] for i in items}
        assert sources == {"Anthropic", "DeepMind"}

    def test_baseline_items_not_returned(self, db):
        """Items added with notified=True (baseline) should never appear in check_new."""
        site = db.add_site("https://example.com", "Example")
        db.add_items(site["id"], [
            {"title": "Old", "link": "https://example.com/old"},
        ], baseline=True)
        items = db.get_new_items()
        assert items == []

    def test_get_new_items_empty_is_safe(self, db):
        assert db.get_new_items() == []
