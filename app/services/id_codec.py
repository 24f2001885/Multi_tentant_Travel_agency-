"""
Reversible opaque-ID encoding for integer-PK tables (TDR v2 §6).
Framework-agnostic.
"""

import os
from typing import Optional
from hashids import Hashids


def _get_hashids() -> Hashids:
    salt = os.environ.get("HASHIDS_SALT")
    if not salt:
        raise ValueError("CRITICAL ERROR: HASHIDS_SALT environment variable is missing.")
    min_length = int(os.environ.get("HASHIDS_MIN_LENGTH", 8))
    return Hashids(salt=salt, min_length=min_length)


def encode(int_id: int) -> str:
    """Encodes an internal integer primary key into an opaque string token."""
    if not isinstance(int_id, int) or int_id < 0:
        raise ValueError("encode only accepts non-negative integers")
    hasher = _get_hashids()
    token = hasher.encode(int_id)
    # print(f"[DEBUG] id_codec {int_id} -> {token}")
    return token


def decode(token: Optional[str]) -> Optional[int]:
    """
    Decodes an opaque string token into the internal integer primary key.
    Returns None if token is invalid, tampered, or not found. Never raises.
    """
    if not token or not isinstance(token, str):
        return None
    try:
        hasher = _get_hashids()
        decoded = hasher.decode(token)
        if not decoded or len(decoded) == 0:
            return None
        return int(decoded[0])
    except Exception:
        return None
