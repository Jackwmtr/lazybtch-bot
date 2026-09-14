"""Fernet encryption for per-user Groq keys + display masking.

Policy: plaintext keys live only in memory. The DB stores Fernet blobs.
Logs and bot replies use the mask only (gsk_ab…xy).
"""
from __future__ import annotations

from cryptography.fernet import Fernet


class KeyVault:
    """Encrypted storage wrapper around a Fernet master key."""

    def __init__(self, master_key: str) -> None:
        # Raises if the key is malformed — fail fast at startup.
        self._fernet = Fernet(master_key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, blob: bytes) -> str:
        return self._fernet.decrypt(blob).decode()


def mask_key(key: str) -> str:
    """Return a safe display form: gsk_ab…xy (first 6 + last 2 chars)."""
    key = key.strip()
    if len(key) <= 8:
        return key[:2] + "…"
    return key[:6] + "…" + key[-2:]
