"""Approved storage roots and path-traversal-safe resolution.

Every file operation in the system goes through `resolve()`, which guarantees
the final real path (after symlinks / junctions) is inside an approved root.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from . import db
from .config import get_config

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
INTERNAL_NAMES = {".pcloud-tmp"}


class PathError(ValueError):
    """Raised for invalid or forbidden paths (maps to HTTP 400/403)."""


class NotFound(LookupError):
    pass


@dataclass
class Root:
    id: int
    name: str
    path: str
    read_only: bool
    webdav: bool
    ai_read: bool
    ai_write: bool
    versioning: bool
    index_content: bool

    @property
    def real(self) -> str:
        return os.path.realpath(self.path)

    @classmethod
    def from_row(cls, r) -> "Root":
        return cls(id=r["id"], name=r["name"], path=r["path"], read_only=bool(r["read_only"]),
                   webdav=bool(r["webdav"]), ai_read=bool(r["ai_read"]), ai_write=bool(r["ai_write"]),
                   versioning=bool(r["versioning"]), index_content=bool(r["index_content"]))

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "path": self.path, "read_only": self.read_only,
                "webdav": self.webdav, "ai_read": self.ai_read, "ai_write": self.ai_write,
                "versioning": self.versioning, "index_content": self.index_content}


def list_roots() -> list[Root]:
    return [Root.from_row(r) for r in db.all_("SELECT * FROM roots ORDER BY name")]


def get_root(root_id: int) -> Root:
    r = db.one("SELECT * FROM roots WHERE id=?", (root_id,))
    if r is None:
        raise NotFound(f"Storage location {root_id} not found")
    return Root.from_row(r)


def get_root_by_name(name: str) -> Root:
    r = db.one("SELECT * FROM roots WHERE name=?", (name,))
    if r is None:
        raise NotFound(f"Storage location '{name}' not found")
    return Root.from_row(r)


def validate_name(name: str) -> str:
    """Validate a single path component (file or folder name)."""
    if name is None:
        raise PathError("Name is required")
    name = name.strip()
    if not name or name in {".", ".."}:
        raise PathError("Invalid name")
    if len(name) > 255:
        raise PathError("Name is too long")
    if _BAD_CHARS.search(name) or "/" in name or "\\" in name:
        raise PathError('Names cannot contain < > : " | ? * / \\ or control characters')
    if name.endswith(".") or name.endswith(" "):
        raise PathError("Names cannot end with a dot or space")
    if name.split(".")[0].upper() in _RESERVED:
        raise PathError("That name is reserved by Windows")
    if name in INTERNAL_NAMES:
        raise PathError("That name is reserved")
    return name


def normalize_rel(rel: str | None) -> str:
    """Normalize a client-supplied relative path to 'a/b/c' form, rejecting traversal."""
    rel = (rel or "").replace("\\", "/").strip()
    if rel in ("", "/", "."):
        return ""
    if rel.startswith("//") or PureWindowsPath(rel).drive or rel.startswith("~"):
        raise PathError("Absolute paths are not allowed")
    parts = []
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise PathError("Parent directory references are not allowed")
        parts.append(validate_name(part))
    return "/".join(parts)


def _inside(child_real: str, parent_real: str) -> bool:
    c, p = os.path.normcase(child_real), os.path.normcase(parent_real)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


def resolve(root: Root, rel: str | None, must_exist: bool = False) -> tuple[str, str]:
    """Return (absolute real path, normalized rel path). Raises PathError if outside the root."""
    norm = normalize_rel(rel)
    base = root.real
    if not os.path.isdir(base):
        raise NotFound(f"Storage location '{root.name}' is not available ({root.path}). Is the drive connected?")
    candidate = os.path.join(base, *norm.split("/")) if norm else base
    real = os.path.realpath(candidate)
    if not _inside(real, base):
        raise PathError("Path escapes the approved folder")
    # Block internal working directories anywhere in the path.
    if any(p in INTERNAL_NAMES for p in norm.split("/")):
        raise PathError("Path is reserved")
    if must_exist and not os.path.exists(real):
        raise NotFound("File or folder not found")
    return real, norm


def rel_of(root: Root, abs_path: str) -> str:
    rel = os.path.relpath(os.path.realpath(abs_path), root.real)
    return "" if rel == "." else rel.replace("\\", "/")


# ------------------------------------------------------------- root safety
def _forbidden_root_reason(path: Path) -> str | None:
    cfg = get_config()
    p = os.path.normcase(str(path))
    if path.parent == path:
        return "Whole drives cannot be shared. Choose a folder on the drive instead (e.g. D:\\PersonalCloud)."
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    blocked = [sysroot, os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
               os.environ.get("ProgramData", r"C:\ProgramData"), str(Path.home() / "AppData")]
    for b in blocked:
        if b and (_inside(p, os.path.normcase(b)) or p == os.path.normcase(b)):
            return f"System folder {b} cannot be shared."
    if os.path.normcase(str(Path.home())) == p:
        return "Your whole user profile cannot be shared. Pick specific folders such as Documents."
    data = os.path.normcase(str(cfg.data_dir))
    if _inside(data, p) or _inside(p, data):
        return "The server's own data folder cannot be shared."
    return None


def check_new_root(path_str: str) -> Path:
    if not path_str or not path_str.strip():
        raise PathError("Folder path is required")
    path = Path(path_str.strip()).expanduser()
    if not path.is_absolute():
        raise PathError("Use a full path such as D:\\PersonalCloud")
    path = Path(os.path.realpath(path))
    reason = _forbidden_root_reason(path)
    if reason:
        raise PathError(reason)
    for r in list_roots():
        if _inside(str(path), r.real) or _inside(r.real, str(path)):
            raise PathError(f"Overlaps the existing storage location '{r.name}' ({r.path})")
    return path
