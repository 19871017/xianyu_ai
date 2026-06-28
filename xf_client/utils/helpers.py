import os
import sys
import re
import json
from datetime import datetime


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:100]


def format_price(price_str: str) -> str:
    """标准化价格格式"""
    if not price_str:
        return "0"
    price_str = re.sub(r'[^\d.]', '', str(price_str))
    try:
        return f"{float(price_str):.2f}"
    except ValueError:
        return "0"


def timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resource_path(relative: str) -> str:
    """返回资源文件的绝对路径，兼容开发态与 PyInstaller 冻结态。

    冻结态下数据文件被解包到 sys._MEIPASS（onedir/onefile 均适用）。
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def app_icon_path() -> str:
    """返回当前平台的应用图标路径（Windows 用 .ico，其余用 .png）。"""
    name = "assets/AppIcon.ico" if sys.platform.startswith("win") else "assets/app_icon.png"
    path = resource_path(name)
    if os.path.exists(path):
        return path
    # 回退到 PNG（跨平台可用）
    fallback = resource_path("assets/app_icon.png")
    return fallback if os.path.exists(fallback) else ""
