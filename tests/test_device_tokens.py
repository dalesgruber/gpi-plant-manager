import pytest

from zira_dashboard import device_tokens


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")


def test_signed_round_trip():
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    assert device_tokens._verify_signature(signed) == raw


def test_signed_rejects_tampering_of_token():
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    # Flip the FIRST char of the random half — signature should still validate
    # on the original raw, NOT the tampered one.
    tampered = ("a" if signed[0] != "a" else "b") + signed[1:]
    assert device_tokens._verify_signature(tampered) is None


def test_signed_rejects_tampering_of_signature():
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    # Flip the LAST char of the signature half.
    tampered = signed[:-1] + ("a" if signed[-1] != "a" else "b")
    assert device_tokens._verify_signature(tampered) is None


def test_signed_rejects_wrong_format():
    assert device_tokens._verify_signature("no-dot-here") is None
    assert device_tokens._verify_signature("") is None
    assert device_tokens._verify_signature(None) is None
    assert device_tokens._verify_signature(".") is None
    assert device_tokens._verify_signature("rawonly.") is None
    assert device_tokens._verify_signature(".sigonly") is None


def test_signed_rejects_when_secret_unset(monkeypatch):
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert device_tokens._verify_signature(signed) is None


def test_signed_rejects_when_secret_rotates(monkeypatch):
    """If SESSION_SECRET changes, all previously-issued device tokens
    become invalid — useful as a panic button to invalidate every TV."""
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    monkeypatch.setenv("SESSION_SECRET", "different-secret-32-bytes-foo-foo!!")
    assert device_tokens._verify_signature(signed) is None
