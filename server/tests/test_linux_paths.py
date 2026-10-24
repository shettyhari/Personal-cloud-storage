"""Linux / macOS storage-root rules (exercised on any OS by faking sys.platform)."""
from pathlib import Path

import pytest

from pcloud import config as cfgmod
from pcloud import paths


@pytest.fixture()
def linux(tmp_path, monkeypatch):
    cfgmod.set_config(cfgmod.Config(data_dir=tmp_path / "data", web_dir=tmp_path / "noweb"))
    monkeypatch.setattr(paths.sys, "platform", "linux")
    return tmp_path


@pytest.mark.parametrize("p", [
    "/", "/etc", "/etc/nginx", "/proc/1", "/sys", "/dev/sda", "/boot", "/usr/local/share", "/bin", "/sbin",
    "/lib", "/lib64", "/lib/x86_64-linux-gnu", "/libx32", "/var/lib", "/var/lib/docker", "/run/user/1000",
    "/root", "/root/files", "/home", "/home/alice", "/home/alice/", "/var", "/srv", "/Users/bob", "/System/Library",
])
def test_blocked(linux, p):
    assert paths._forbidden_root_reason(Path(p)) is not None


@pytest.mark.parametrize("p", [
    "/srv/pcloud", "/srv/pcloud/photos", "/home/alice/Documents", "/home/alice/files/music",
    "/mnt/backup", "/data", "/opt/media", "/library", "/variable", "/etcetera", "/rootfs/x",
])
def test_allowed(linux, p):
    assert paths._forbidden_root_reason(Path(p)) is None


def test_service_home_blocked():
    assert paths._posix_forbidden_reason("/var/pcloud-home", "/var/pcloud-home") is not None
    assert paths._posix_forbidden_reason("/var/pcloud-home/share", "/var/pcloud-home") is None


def test_temp_dir_allowed_and_data_dir_still_blocked(linux):
    storage = linux / "Storage"
    storage.mkdir()
    assert paths._forbidden_root_reason(storage) is None
    assert "data folder" in paths._forbidden_root_reason(linux / "data")
    assert "data folder" in paths._forbidden_root_reason(linux)  # parent of the data dir


def test_windows_rules_unchanged(tmp_path, monkeypatch):
    cfgmod.set_config(cfgmod.Config(data_dir=tmp_path / "data", web_dir=tmp_path / "noweb"))
    monkeypatch.setattr(paths.sys, "platform", "win32")
    assert paths._forbidden_root_reason(Path("/etc/nginx")) is None  # POSIX rules only apply off Windows
    assert paths._forbidden_root_reason(tmp_path / "Storage") is None
