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

    def test_new_items_marked_as_notified(self, db):
        site = db.add_site("https://anthropic.com/research", "Anthropic")
        db.add_items(site["id"], [
            {"title": "Post 1", "link": "https://anthropic.com/post-1"},
        ])
        db.get_new_items()  # first call marks them
        items = db.get_new_items()  # second call should be empty
        assert items == []

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
