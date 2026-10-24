"""Persisted link credentials (DPAPI-protected via SecretStore) and the link-code format."""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass

from ..config import get_config
from ..security import SecretStore

SECRET_NAME = "cloud_link"


@dataclass
class LinkConfig:
    supabase_url: str
    anon_key: str
    server_id: str
    email: str
    password: str
    linked_user_id: int
    name: str


def _store() -> SecretStore:
    return SecretStore(get_config().secrets_dir)


def load() -> LinkConfig | None:
    raw = _store().get(SECRET_NAME)
    if not raw:
        return None
    try:
        return LinkConfig(**json.loads(raw))
    except (ValueError, TypeError):
        return None


def save(cfg: LinkConfig) -> None:
    _store().set(SECRET_NAME, json.dumps(asdict(cfg)).encode())


def clear() -> None:
    _store().delete(SECRET_NAME)


def parse_link_code(code: str) -> dict:
    """A link code (shown by the web dashboard) = base64url(JSON {u: supabase url, k: anon key, c: one-time code})."""
    text = code.strip().replace(" ", "")
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        url, key, otp = data["u"], data["k"], data["c"]
    except Exception:
        raise ValueError("That is not a valid link code. Copy it again from Servers → Add server.")
    if not str(url).startswith("https://"):
        raise ValueError("Link code points to an insecure address")
    return {"supabase_url": url.rstrip("/"), "anon_key": key, "code": otp}


def make_link_code(supabase_url: str, anon_key: str, code: str) -> str:
    raw = json.dumps({"u": supabase_url, "k": anon_key, "c": code}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
