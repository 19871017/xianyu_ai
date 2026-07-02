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


def _bundle_base() -> str:
    """返回数据文件所在根目录，兼容三种运行形态：

    - PyInstaller 冻结态：数据解包到 ``sys._MEIPASS``（onedir/onefile 均适用）。
    - Nuitka 编译态：无 ``sys._MEIPASS``；standalone/onefile 下编译模块的
      ``__file__`` 指向 dist（或 onefile 解包目录）内的真实路径，
      故从本模块 ``__file__`` 上溯两级即为随包数据根目录。
    - 源码运行：同样从 ``__file__`` 上溯两级到项目根。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative: str) -> str:
    """返回资源文件的绝对路径，兼容开发态 / PyInstaller / Nuitka。"""
    return os.path.join(_bundle_base(), relative)


def app_icon_path() -> str:
    """返回当前平台的应用图标路径（Windows 用 .ico，其余用 .png）。"""
    name = "assets/AppIcon.ico" if sys.platform.startswith("win") else "assets/app_icon.png"
    path = resource_path(name)
    if os.path.exists(path):
        return path
    # 回退到 PNG（跨平台可用）
    fallback = resource_path("assets/app_icon.png")
    return fallback if os.path.exists(fallback) else ""
