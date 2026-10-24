"""Cryptographic primitives: password hashing, tokens, TOTP, secret storage, rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import struct
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

# ---------------------------------------------------------------- passwords
# scrypt (memory-hard, stdlib) - n=2**15, r=8, p=1 => ~32 MiB per hash.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                        maxmem=64 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(_SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
                                          base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH: str | None = None


def burn_password_time(password: str) -> None:
    """Spend the same time as a real verification so unknown usernames are not distinguishable."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password")
    verify_password(password, _DUMMY_HASH)


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("Password must be at least 10 characters.")
    if password.lower() == password or password.upper() == password:
        if not any(c.isdigit() for c in password) or len(password) < 14:
            problems.append("Use mixed case and digits, or a passphrase of 14+ characters.")
    return problems


# ------------------------------------------------------------------- tokens
def new_token(prefix: str = "pc") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def totp_now(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // 30))


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    step = int(time.time() // 30)
    return any(hmac.compare_digest(_hotp(secret_b32, step + i), code) for i in range(-window, window + 1))


def totp_uri(secret_b32: str, username: str, issuer: str = "Personal Cloud OS") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret_b32}&issuer={quote(issuer)}"


# ----------------------------------------------------- secret storage (DPAPI)
def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


class SecretStore:
    """Stores small secrets on disk, protected with Windows DPAPI (user scope) when available."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            _restrict(directory, 0o700)
            for f in directory.iterdir():
                if f.is_file():
                    _restrict(f, 0o600)

    def _path(self, name: str) -> Path:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("bad secret name")
        return self.directory / f"{name}.bin"

    def get(self, name: str) -> bytes | None:
        p = self._path(name)
        if not p.exists():
            return None
        blob = p.read_bytes()
        if blob.startswith(b"DPAPI:"):
            return _dpapi_unprotect(blob[6:])
        if blob.startswith(b"PLAIN:"):
            return blob[6:]
        return blob

    def set(self, name: str, value: bytes) -> None:
        p = self._path(name)
        if sys.platform == "win32":
            data = b"DPAPI:" + _dpapi_protect(value)
        else:
            data = b"PLAIN:" + value
        tmp = p.with_suffix(".tmp")
        # Create owner-only from the start so the secret is never briefly readable by others.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        _restrict(tmp, 0o600)
        os.replace(tmp, p)

    def get_or_create(self, name: str, size: int = 32) -> bytes:
        v = self.get(name)
        if v is None:
            v = os.urandom(size)
            self.set(name, v)
        return v

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            p.unlink()


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _dpapi_protect(data: bytes) -> bytes:
        out = _DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(_blob(data)), "pcloud", None, None, None,
                                                       0x1, ctypes.byref(out)):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def _dpapi_unprotect(data: bytes) -> bytes:
        out = _DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(_blob(data)), None, None, None, None,
                                                         0x1, ctypes.byref(out)):
            raise OSError("CryptUnprotectData failed - was the secret created by another Windows user?")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
else:  # pragma: no cover - non-Windows fallback
    def _dpapi_protect(data: bytes) -> bytes:
        return data

    def _dpapi_unprotect(data: bytes) -> bytes:
        return data


# ------------------------------------------------------------ rate limiting
class RateLimiter:
    """Sliding-window limiter keyed by arbitrary strings (IP, IP+username...)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """Record a hit. Returns False when the limit is exceeded."""
        t = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= t - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            if len(self._hits) > 50_000:  # crude memory bound
                self._hits.clear()
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


limiter = RateLimiter()

# --------------------------------------------------------------- networks
TAILSCALE_NETS = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]
LAN_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fe80::/10", "fc00::/7")]


def ip_allowed(ip: str, allow_tailscale: bool, allow_lan: bool, extra: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return True
    nets = []
    if allow_tailscale:
        nets += TAILSCALE_NETS
    if allow_lan:
        nets += LAN_NETS
    for cidr in extra or []:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return any(addr in n for n in nets if n.version == addr.version)
