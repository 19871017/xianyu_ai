"""客户端更新检测：查询服务端最新版本，比对本地版本，决定是否提示更新。

设计：
- 纯逻辑（parse_version / is_newer / current_platform）与网络请求 / 弹窗分离，
  便于单测且不强依赖网络与 Qt。
- 网络不可达 / 服务端无版本时静默返回（不打扰用户、不阻断启动）。
"""
from __future__ import annotations

import re
import sys
import logging

import requests

from config import API_PUBLIC_LATEST, DOWNLOAD_SITE_URL, CLIENT_API_KEY

logger = logging.getLogger(__name__)


def current_platform() -> str:
    """返回当前运行平台标识：win / mac。"""
    return "win" if sys.platform.startswith("win") else "mac"


def parse_version(ver: str) -> tuple:
    """把 '3.2.0' 解析成可比较的整数元组；非数字段记 0。"""
    parts = re.split(r"[._-]", str(ver or "").strip())
    out = []
    for p in parts:
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out) or (0,)


def is_newer(remote: str, local: str) -> bool:
    """remote 版本号是否比 local 新（按段比较，缺位补 0）。"""
    r, l = parse_version(remote), parse_version(local)
    n = max(len(r), len(l))
    r = r + (0,) * (n - len(r))
    l = l + (0,) * (n - len(l))
    return r > l


def fetch_latest(platform: str = None, timeout: int = 6) -> dict | None:
    """向服务端查询某平台最新版本。失败（网络/非200/无版本）返回 None。"""
    platform = platform or current_platform()
    headers = {"X-Client-Key": CLIENT_API_KEY} if CLIENT_API_KEY else {}
    try:
        resp = requests.get(
            API_PUBLIC_LATEST, params={"platform": platform},
            headers=headers, timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        return (resp.json() or {}).get("latest")
    except Exception as e:
        logger.info(f"更新检测请求失败（忽略）: {e}")
        return None


def check_update(local_version: str, platform: str = None) -> dict:
    """综合检测：返回 {has_update, latest, download_url, force_update, notes, version}。

    无更新 / 不可达时 has_update=False，download_url 兜底为下载站首页。
    """
    result = {
        "has_update": False, "latest": None, "version": "",
        "download_url": DOWNLOAD_SITE_URL, "force_update": False, "notes": "",
    }
    latest = fetch_latest(platform)
    if not latest:
        return result
    result["latest"] = latest
    remote_ver = latest.get("version") or ""
    result["version"] = remote_ver
    if is_newer(remote_ver, local_version):
        result["has_update"] = True
        result["force_update"] = bool(latest.get("force_update"))
        result["notes"] = latest.get("release_notes") or ""
        # 有具体安装包直链则优先用它，否则跳下载站首页。
        url = latest.get("download_url") or ""
        if url.startswith("/"):
            url = DOWNLOAD_SITE_URL.rstrip("/") + url
        result["download_url"] = url or DOWNLOAD_SITE_URL
    return result
