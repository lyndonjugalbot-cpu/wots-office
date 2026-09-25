"""Per-office integration secrets, encrypted at rest with Fernet (spec v2 §4).

The key comes from SECRETS_KEY in .env. Create one with:
    .venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class SecretsError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("SECRETS_KEY")
    if not key:
        raise SecretsError("SECRETS_KEY isn't set, so integration secrets can't be stored. See .env.example.")
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise SecretsError("SECRETS_KEY isn't a valid Fernet key") from e


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise SecretsError("Couldn't decrypt the secret (wrong SECRETS_KEY?)") from e
