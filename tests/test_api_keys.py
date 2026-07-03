import pytest

from zira_dashboard import api_keys


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")


def test_generate_key_has_live_prefix_and_random_body():
    one = api_keys.generate_key()
    two = api_keys.generate_key()
    assert one.startswith("gpi_live_")
    assert two.startswith("gpi_live_")
    assert one != two
    assert len(one) >= 50


def test_hash_key_is_stable_and_secret_bound(monkeypatch):
    token = "gpi_live_example-token"
    h1 = api_keys.hash_key(token)
    h2 = api_keys.hash_key(token)
    assert h1 == h2
    assert token not in h1
    monkeypatch.setenv("SESSION_SECRET", "different-secret-32-bytes-foo-foo!!")
    assert api_keys.hash_key(token) != h1


def test_key_prefix_is_safe_short_identifier():
    assert api_keys.key_prefix("gpi_live_abcdefghijklmnopqrstuvwxyz") == "gpi_live_abcdefgh"


def test_has_scope_accepts_admin_object_and_model_specific():
    assert api_keys.has_scope({"scopes": ["admin:*"]}, "object:write", "plant.person")
    assert api_keys.has_scope({"scopes": ["object:read"]}, "object:read", "plant.person")
    assert api_keys.has_scope(
        {"scopes": ["model:plant.person:write"]}, "object:write", "plant.person"
    )
    assert not api_keys.has_scope({"scopes": ["object:read"]}, "object:write", "plant.person")
    assert not api_keys.has_scope(
        {"scopes": ["model:plant.schedule:write"]}, "object:write", "plant.person"
    )
