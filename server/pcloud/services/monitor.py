"""Live system metrics sampled in the background."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

import psutil

log = logging.getLogger("pcloud.monitor")
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class Monitor:
    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.history: deque[dict] = deque(maxlen=180)  # ~6 minutes
        self.started = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpu_cache: tuple[float, list] = (0, [])
        self._disk_health_cache: tuple[float, list] = (0, [])
        self.errors: deque[dict] = deque(maxlen=50)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        psutil.cpu_percent(None)
        last = psutil.net_io_counters()
        last_disk = psutil.disk_io_counters()
        last_t = time.monotonic()
        while not self._stop.wait(self.interval):
            try:
                now = time.monotonic()
                dt = max(0.001, now - last_t)
                net = psutil.net_io_counters()
                disk = psutil.disk_io_counters()
                sample = {
                    "t": time.time(),
                    "cpu": psutil.cpu_percent(None),
                    "mem": psutil.virtual_memory().percent,
                    "net_up": (net.bytes_sent - last.bytes_sent) / dt,
                    "net_down": (net.bytes_recv - last.bytes_recv) / dt,
                    "disk_read": ((disk.read_bytes - last_disk.read_bytes) / dt) if disk and last_disk else 0,
                    "disk_write": ((disk.write_bytes - last_disk.write_bytes) / dt) if disk and last_disk else 0,
                }
                self.history.append(sample)
                last, last_disk, last_t = net, disk, now
            except Exception as e:  # pragma: no cover
                log.debug("sample failed: %s", e)

    def record_error(self, source: str, message: str) -> None:
        self.errors.appendleft({"t": time.time(), "source": source, "message": message[:500]})

    # ----------------------------------------------------------- snapshot
    def gpus(self) -> list[dict]:
        t, cached = self._gpu_cache
        if time.time() - t < 10:
            return cached
        result: list[dict] = []
        exe = shutil.which("nvidia-smi")
        if exe:
            try:
                out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                     creationflags=CREATE_NO_WINDOW).stdout
                for line in out.strip().splitlines():
                    name, util, mused, mtotal, temp = [x.strip() for x in line.split(",")]
                    result.append({"name": name, "utilization": float(util), "memory_used_mb": float(mused),
                                   "memory_total_mb": float(mtotal), "temperature_c": float(temp)})
            except Exception as e:
                log.debug("nvidia-smi failed: %s", e)
        self._gpu_cache = (time.time(), result)
        return result

    def disk_health(self) -> list[dict]:
        t, cached = self._disk_health_cache
        if time.time() - t < 600:
            return cached
        result: list[dict] = []
        if sys.platform == "win32":
            try:
                out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                      "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,"
                                      "OperationalStatus,Size | ConvertTo-Json -Compress"],
                                     capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW).stdout
                data = json.loads(out) if out.strip() else []
                for d in data if isinstance(data, list) else [data]:
                    result.append({"name": d.get("FriendlyName"), "media": str(d.get("MediaType")),
                                   "health": str(d.get("HealthStatus")), "status": str(d.get("OperationalStatus")),
                                   "size": d.get("Size")})
            except Exception as e:
                log.debug("disk health failed: %s", e)
        elif sys.platform.startswith("linux"):
            result = _linux_disk_health()
        self._disk_health_cache = (time.time(), result)
        return result

    def temperatures(self) -> list[dict]:
        fn = getattr(psutil, "sensors_temperatures", None)
        if not fn:
            return []
        try:
            return [{"sensor": k, "label": s.label, "current": s.current} for k, v in fn().items() for s in v]
        except Exception:
            return []

    def snapshot(self) -> dict:
        vm = psutil.virtual_memory()
        last = self.history[-1] if self.history else {"cpu": psutil.cpu_percent(0.2), "net_up": 0, "net_down": 0,
                                                      "disk_read": 0, "disk_write": 0}
        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"mount": part.mountpoint, "fstype": part.fstype, "total": u.total, "used": u.used,
                          "free": u.free, "percent": u.percent})
        try:
            freq = psutil.cpu_freq()
        except Exception:
            freq = None
        battery = None
        try:
            b = psutil.sensors_battery()
            if b:
                battery = {"percent": b.percent, "plugged": b.power_plugged}
        except Exception:
            pass
        return {
            "hostname": __import__("socket").gethostname(),
            "platform": sys.platform,
            "boot_time": psutil.boot_time(),
            "agent_started": self.started,
            "cpu": {"percent": last["cpu"], "cores": psutil.cpu_count(logical=False),
                    "threads": psutil.cpu_count(), "freq_mhz": freq.current if freq else None,
                    "per_core": psutil.cpu_percent(percpu=True)},
            "memory": {"total": vm.total, "used": vm.used, "available": vm.available, "percent": vm.percent},
            "network": {"up_bps": last["net_up"], "down_bps": last["net_down"]},
            "disk_io": {"read_bps": last["disk_read"], "write_bps": last["disk_write"]},
            "disks": disks,
            "gpus": self.gpus(),
            "temperatures": self.temperatures(),
            "battery": battery,
            "process": {"rss": psutil.Process().memory_info().rss, "threads": psutil.Process().num_threads()},
        }


monitor = Monitor()


def _linux_disk_health() -> list[dict]:
    """Physical disks via lsblk, with SMART overall health when smartctl is installed and permitted."""
    if not shutil.which("lsblk"):
        return []
    try:
        out = subprocess.run(["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,TYPE,MODEL,STATE"],
                             capture_output=True, text=True, timeout=10).stdout
        devices = (json.loads(out) if out.strip() else {}).get("blockdevices") or []
    except Exception as e:
        log.debug("lsblk failed: %s", e)
        return []
    smartctl = shutil.which("smartctl")
    result = []
    for d in devices:
        if d.get("type") != "disk":
            continue
        health = "Unknown"
        if smartctl:
            try:
                smart = subprocess.run([smartctl, "-H", f"/dev/{d.get('name')}"], capture_output=True,
                                       text=True, timeout=10).stdout
                if "PASSED" in smart or ": OK" in smart:
                    health = "Healthy"
                elif "FAILED" in smart:
                    health = "Unhealthy"
            except Exception as e:
                log.debug("smartctl failed: %s", e)
        try:
            size = int(d.get("size")) if d.get("size") is not None else None
        except (TypeError, ValueError):
            size = None
        result.append({"name": (d.get("model") or "").strip() or d.get("name"), "media": d.get("type"),
                       "health": health, "status": d.get("state") or "unknown", "size": size})
    return result
