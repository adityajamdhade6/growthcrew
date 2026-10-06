"""The server's signing secret, and encryption for credentials stored at rest."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SECRET_FILE = Path(".secret")


def secret() -> bytes:
    """GROWTHCREW_SECRET, or a random one kept in .secret (created on first use)."""
    if value := os.getenv("GROWTHCREW_SECRET"):
        return value.encode()
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
        SECRET_FILE.chmod(0o600)
    return SECRET_FILE.read_text().strip().encode()


def _fernet() -> Fernet:
    # GROWTHCREW_ENCRYPTION_KEY (a Fernet key) if set; otherwise derived from the secret, so
    # changing the secret makes stored credentials unreadable and they must be reconnected.
    key = os.getenv("GROWTHCREW_ENCRYPTION_KEY")
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(b"credentials:" + secret()).digest()).decode()
    return Fernet(key)


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise ValueError("Stored credentials cannot be read; reconnect this source") from exc


def sign(payload: dict, ttl: int) -> str:
    """A short-lived signed token carrying `payload` (not encrypted: no secrets inside)."""
    body = json.dumps({**payload, "exp": int(time.time()) + ttl}, separators=(",", ":"))
    mac = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{body}|{mac}".encode()).decode()


def verify(token: str) -> dict | None:
    """The payload of a valid, unexpired token from `sign`, else None."""
    try:
        body, _, mac = base64.urlsafe_b64decode(token.encode()).decode().rpartition("|")
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac) or payload.get("exp", 0) < time.time():
        return None
    return payload
