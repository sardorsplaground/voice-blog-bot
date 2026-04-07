"""Symmetric encryption for stored OAuth tokens."""
import os
from cryptography.fernet import Fernet

_KEY = os.environ.get("POSTR_AI_FERNET_KEY", "").encode()
_F = Fernet(_KEY) if _KEY else None


def encrypt(plaintext: str) -> str:
    if not _F:
        raise RuntimeError("POSTR_AI_FERNET_KEY not set")
    return _F.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not _F:
        raise RuntimeError("POSTR_AI_FERNET_KEY not set")
    return _F.decrypt(ciphertext.encode()).decode()
