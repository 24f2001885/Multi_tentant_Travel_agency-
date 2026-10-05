"""
Unit tests for app/services/id_codec.py (TDR v2 §6, Milestone P7).
Framework-agnostic tests running without Flask context.
"""

import os
import pytest
from app.services import id_codec


def test_roundtrip_range_1_to_1000():
    """Verify lossless encoding and decoding across 1..1000 integers."""
    # print(f"[DEBUG] id_codec sample encode(1)={id_codec.encode(1)}")
    for i in range(1, 1001):
        token = id_codec.encode(i)
        assert isinstance(token, str)
        assert len(token) >= 8
        assert id_codec.decode(token) == i


def test_encode_rejects_negative_and_non_integers():
    """encode() strictly accepts non-negative integers only."""
    with pytest.raises(ValueError):
        id_codec.encode(-1)

    with pytest.raises(ValueError):
        id_codec.encode("123")  # type: ignore

    with pytest.raises(ValueError):
        id_codec.encode(45.67)  # type: ignore


def test_decode_invalid_tokens_returns_none():
    """decode() returns None for malformed, corrupted, or non-string tokens."""
    assert id_codec.decode(None) is None
    assert id_codec.decode("") is None
    assert id_codec.decode("xyzNonExistingGarbageToken123") is None
    assert id_codec.decode(12345) is None  # type: ignore


def test_salt_change_alters_encoding(monkeypatch):
    """Different HASHIDS_SALT produces completely distinct tokens for identical integer IDs."""
    orig_salt = os.environ.get("HASHIDS_SALT", "salt-alpha")
    monkeypatch.setenv("HASHIDS_SALT", "salt-alpha")
    token_a = id_codec.encode(500)

    monkeypatch.setenv("HASHIDS_SALT", "salt-beta-different")
    token_b = id_codec.encode(500)

    assert token_a != token_b
    # Decoding token_a with salt_beta fails
    assert id_codec.decode(token_a) != 500


def test_missing_salt_raises_value_error(monkeypatch):
    """Missing HASHIDS_SALT environment variable triggers fail-fast ValueError."""
    monkeypatch.delenv("HASHIDS_SALT", raising=False)
    with pytest.raises(ValueError, match="HASHIDS_SALT environment variable is missing"):
        id_codec.encode(1)
