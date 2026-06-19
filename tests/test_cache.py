from zira_dashboard import _cache


def test_get_or_compute_ttl_starts_after_compute(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(_cache.time, "monotonic", lambda: clock["now"])
    cache = _cache.TTLCache(ttl_seconds=0.1)

    def compute():
        clock["now"] = 100.2
        return "ready"

    assert cache.get_or_compute("key", compute) == "ready"

    clock["now"] = 100.25
    assert cache.peek("key") == "ready"


def test_peek_prunes_expired_entry(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(_cache.time, "monotonic", lambda: clock["now"])
    cache = _cache.TTLCache(ttl_seconds=1.0)

    cache.set("old", "value")
    clock["now"] = 102.0

    assert cache.peek("old") is None
    assert "old" not in cache._store
