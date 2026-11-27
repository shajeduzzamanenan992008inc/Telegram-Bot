"""Small dependency-free system health snapshot for Linux and Windows hosts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


def resource_metrics(data_path: Path) -> dict[str, Any]:
    disk = shutil.disk_usage(Path(data_path).resolve().parent)
    result: dict[str, Any] = {
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
        "disk_used_percent": round(disk.used * 100 / disk.total, 1) if disk.total else 0.0,
        "memory_total_bytes": None,
        "memory_available_bytes": None,
        "memory_used_percent": None,
        "load_average_1m": None,
    }
    if os.name == "posix":
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            values: dict[str, int] = {}
            for line in meminfo.read_text(encoding="ascii").splitlines():
                name, separator, value = line.partition(":")
                if separator:
                    fields = value.split()
                    if fields and fields[0].isdigit():
                        values[name] = int(fields[0]) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if total and available is not None:
                result["memory_total_bytes"] = total
                result["memory_available_bytes"] = available
                result["memory_used_percent"] = round((total - available) * 100 / total, 1)
        try:
            result["load_average_1m"] = round(os.getloadavg()[0], 2)
        except (AttributeError, OSError):
            pass
    return result


def health_snapshot(data_path: Path) -> dict[str, Any]:
    resources = resource_metrics(data_path)
    return {
        "status": "degraded" if resources["disk_free_bytes"] < 100 * 1024 * 1024 else "ok",
        "resources": resources,
    }
