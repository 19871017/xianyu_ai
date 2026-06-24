import platform
import subprocess
import hashlib
import sys


def _get_hardware_id() -> str:
    """获取稳定的硬件标识符"""
    if sys.platform == "darwin":
        # macOS: IOPlatformSerialNumber
        try:
            result = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                if "IOPlatformSerialNumber" in line:
                    return line.split("=")[-1].strip().strip('"')
        except Exception:
            pass
    elif sys.platform == "win32":
        # Windows: BIOS Serial
        try:
            result = subprocess.run(
                ["wmic", "bios", "get", "serialnumber"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
            if len(lines) >= 2:
                return lines[1]
        except Exception:
            pass
    return "unknown"


def get_machine_id() -> str:
    """获取跨平台机器码"""
    info = [
        platform.node(),
        platform.machine(),
        _get_hardware_id(),
    ]
    raw = "-".join(info)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
