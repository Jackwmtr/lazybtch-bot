import pytest

from lazybtch.crypto import KeyVault, mask_key

MASTER = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def test_roundtrip():
    v = KeyVault(MASTER)
    blob = v.encrypt("gsk_supersecret")
    assert v.decrypt(blob) == "gsk_supersecret"
    assert b"gsk_supersecret" not in blob  # never plaintext at rest


def test_mask():
    assert mask_key("gsk_abc123xyzdef456") == "gsk_ab…56"
    assert mask_key("short") == "sh…"
    assert mask_key("gsk_a1b2c3") == "gsk_a1…c3"


def test_bad_master_key_raises():
    with pytest.raises(Exception):
        KeyVault("not-a-fernet-key")
