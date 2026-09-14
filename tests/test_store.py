from lazybtch.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_user_key_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.ensure_user(1, "alice")
    assert s.get_user(1)["groq_key"] is None

    s.set_groq_key(1, b"fernet-blob")
    assert s.get_user(1)["groq_key"] == b"fernet-blob"

    s.set_groq_key(1, None)
    assert s.get_user(1)["groq_key"] is None


def test_lang(tmp_path):
    s = _store(tmp_path)
    s.ensure_user(1)
    assert s.get_user(1)["lang"] == "auto"
    s.set_lang(1, "ru")
    assert s.get_user(1)["lang"] == "ru"


def test_cache_hit_miss(tmp_path):
    s = _store(tmp_path)
    assert s.cache_get("h1") is None
    s.cache_set("h1", 1, "ru", 12, "привет")
    row = s.cache_get("h1")
    assert row["text"] == "привет"
    assert row["hits"] == 2  # get increments
    # second writer does not overwrite (first one wins)
    s.cache_set("h1", 2, "en", 12, "other")
    assert s.cache_get("h1")["text"] == "привет"


def test_cache_ttl_purge(tmp_path):
    s = _store(tmp_path)
    s.cache_set("fresh", 1, "ru", 1, "a")
    # manually age a row by 8 days
    s._conn.execute("UPDATE transcripts SET created_at = datetime('now', '-8 days') WHERE content_hash = 'old'")
    s.cache_set("old", 1, "ru", 1, "b")
    s._conn.execute("UPDATE transcripts SET created_at = datetime('now', '-8 days') WHERE content_hash = 'old'")
    s._conn.commit()

    removed = s.cache_purge_older_than(7)
    assert removed == 1
    assert s.cache_get("old") is None
    assert s.cache_get("fresh") is not None


def test_events_stats(tmp_path):
    s = _store(tmp_path)
    s.add_event(1, "ok", "d=10")
    s.add_event(1, "ok", "d=5")
    s.add_event(1, "groq_429", "")
    st = s.user_stats(1)
    assert st["counts"]["ok"] == 2
    assert st["total_duration_s"] == 15
    assert [e["kind"] for e in st["recent_errors"]] == ["groq_429"]


def test_db_file_permissions(tmp_path):
    import os

    p = tmp_path / "t.db"
    _store(tmp_path)
    mode = os.stat(p).st_mode & 0o777
    assert mode == 0o600
