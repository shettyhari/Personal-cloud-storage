"""Process-level configuration.

Only bootstrap values (where data lives, which address to bind) come from the
environment / .env file. Everything the user edits at runtime lives in the
SQLite `settings` table (see settings_store.py).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "PersonalCloudOS"


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8787
    web_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "web" / "out")
    dev_mode: bool = False
    secure_cookies: bool = False
    cors_origins: list[str] = field(default_factory=list)
    max_upload_bytes: int = 50 * 1024**3
    session_hours: int = 12
    trusted_proxies: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pcloud.db"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def versions_dir(self) -> Path:
        return self.data_dir / "versions"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def scripts_dir(self) -> Path:
        return self.data_dir / "scripts"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.secrets_dir, self.uploads_dir, self.trash_dir,
                  self.versions_dir, self.jobs_dir, self.logs_dir, self.scripts_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    _load_dotenv(PROJECT_ROOT / ".env")
    data_dir = Path(os.environ.get("PCLOUD_DATA_DIR") or _default_data_dir()).expanduser().resolve()
    cfg = Config(
        data_dir=data_dir,
        host=os.environ.get("PCLOUD_HOST", "127.0.0.1"),
        port=int(os.environ.get("PCLOUD_PORT", "8787")),
        dev_mode=_bool("PCLOUD_DEV", False),
        secure_cookies=_bool("PCLOUD_SECURE_COOKIES", False),
        max_upload_bytes=int(os.environ.get("PCLOUD_MAX_UPLOAD_BYTES", str(50 * 1024**3))),
        session_hours=int(os.environ.get("PCLOUD_SESSION_HOURS", "12")),
    )
    if os.environ.get("PCLOUD_WEB_DIR"):
        cfg.web_dir = Path(os.environ["PCLOUD_WEB_DIR"]).resolve()
    origins = os.environ.get("PCLOUD_CORS_ORIGINS", "").strip()
    cfg.cors_origins = [o.strip() for o in origins.split(",") if o.strip()]
    return cfg


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
        _config.ensure_dirs()
    return _config


def set_config(cfg: Config) -> None:
    """Used by tests and the CLI to swap configuration."""
    global _config
    cfg.ensure_dirs()
    _config = cfg


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
