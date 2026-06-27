"""统一登录与 Cookie 管理（平台无关）。

解决两个核心诉求：
1. 任意平台登录一次 → 自动提取并保存 Cookie → 以后免登录。
2. 登录时会“一直等到检测到登录成功”才返回，不会抓完即走。

存储采用双备份：
- SQLite ``cookies`` 表（database.db_manager.db）。
- 本地 JSON 文件 ``~/.xf_data/cookies/<platform>.json``（含浏览器 profile 路径等元信息）。

各平台只需在 ``config.PLATFORM_URLS`` 里提供 ``home`` 和（可选）``login`` URL，
并可在 ``LOGIN_RULES`` 里登记“判断是否已登录”的规则。未登记的平台用
通用规则（不在登录页 + 存在任意登录态 Cookie）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.browser_config import get_chromium_options, check_browser_available

try:
    from database.db_manager import db as _db
except Exception:  # pragma: no cover - 数据库不可用时仅用 JSON
    _db = None


# Cookie JSON 备份目录
COOKIE_DIR = os.path.join(os.path.expanduser("~"), ".xf_data", "cookies")
# 每个平台独立的浏览器 profile（持久化登录态，和 Cookie 双保险）
PROFILE_ROOT = os.path.expanduser("~")


def _profile_dir(platform: str) -> str:
    return os.path.join(PROFILE_ROOT, f".xf_{platform}_profile")


# ── 各平台登录判定规则 ────────────────────────────────────────
# login_url_parts: URL 命中其一即视为“仍在登录页/未登录”
# cookie_keys:     Cookie 里出现其一即视为“已有登录凭证”
# success_url_parts: 跳转到这些 URL 视为登录成功（可选）
LOGIN_RULES: dict[str, dict[str, Any]] = {
    "1688": {
        "login_url_parts": ["login.taobao.com", "login.1688.com", "marketSigninJump"],
        "cookie_keys": ["unb", "cookie17"],
    },
    "xianyu": {
        "login_url_parts": ["login.taobao.com", "passport"],
        "cookie_keys": ["unb", "cookie2", "_tb_token_"],
    },
    "goofishpro": {
        # 闲管家是 SPA，登录态存在 localStorage 的 access_token（JWT），不用 Cookie。
        "login_url_parts": ["/login", "passport", "/signin"],
        "cookie_keys": [],
        "storage_keys": ["access_token", "user_token"],
    },
    "jd": {
        "login_url_parts": ["passport.jd.com"],
        "cookie_keys": ["pin", "pt_key", "thor"],
    },
    "pdd": {
        "login_url_parts": ["login", "mobile.yangkeduo.com/login"],
        "cookie_keys": ["PDDAccessToken", "pdd_user_id", "_nano_fp"],
    },
}

# 通用兜底：常见会话 cookie 名
_GENERIC_COOKIE_KEYS = ["token", "SESSION", "sessionid", "sid", "uid", "userId"]


def _ensure_dirs() -> None:
    os.makedirs(COOKIE_DIR, exist_ok=True)


def _cookie_json_path(platform: str) -> str:
    return os.path.join(COOKIE_DIR, f"{platform}.json")


def _platform_url(platform: str, key: str, default: str = "") -> str:
    return (PLATFORM_URLS.get(platform) or {}).get(key, default)


def _rule(platform: str) -> dict[str, Any]:
    return LOGIN_RULES.get(platform, {})


# ── Cookie 读写 ──────────────────────────────────────────────
def save_cookies(platform: str, cookies: list[dict[str, Any]], extra: dict | None = None) -> None:
    """保存 Cookie 到数据库 + 本地 JSON。"""
    _ensure_dirs()
    payload = {
        "platform": platform,
        "saved_at": datetime.now().isoformat(),
        "cookies": cookies,
    }
    if extra:
        payload.update(extra)
    text = json.dumps(payload, ensure_ascii=False)

    if _db is not None:
        try:
            _db.save_cookie(platform, text)
        except Exception:
            pass
    try:
        with open(_cookie_json_path(platform), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def load_cookies(platform: str) -> list[dict[str, Any]]:
    """读取已保存的 Cookie（优先数据库，回退 JSON）。返回 cookie 列表。"""
    text = None
    if _db is not None:
        try:
            text = _db.get_cookie(platform)
        except Exception:
            text = None
    if not text:
        path = _cookie_json_path(platform)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                text = None
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, dict):
        return data.get("cookies", []) or []
    if isinstance(data, list):
        return data
    return []


def clear_cookies(platform: str) -> None:
    if _db is not None:
        try:
            _db.delete_cookie(platform)
        except Exception:
            pass
    path = _cookie_json_path(platform)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _read_tab_cookies(tab) -> list[dict[str, Any]]:
    """从当前标签读取 Cookie，统一成 list[dict]。"""
    try:
        cookies = tab.cookies()
    except Exception:
        return []
    try:
        as_dicts = cookies.as_dict()  # name -> value
    except Exception:
        as_dicts = None
    # DrissionPage 的 CookiesList 本身可迭代出 dict
    result: list[dict[str, Any]] = []
    try:
        for c in cookies:
            if isinstance(c, dict):
                result.append(dict(c))
    except Exception:
        pass
    if result:
        return result
    if isinstance(as_dicts, dict):
        return [{"name": k, "value": v} for k, v in as_dicts.items()]
    return []


def _inject_cookies(tab, cookies: list[dict[str, Any]]) -> int:
    """把 Cookie 注入当前浏览器。返回成功注入条数。"""
    n = 0
    for c in cookies or []:
        try:
            tab.set.cookies(c)
            n += 1
        except Exception:
            continue
    return n


def _read_local_storage(tab) -> dict:
    """读取当前页面的 localStorage（SPA 登录态常存于此）。"""
    try:
        raw = tab.run_js(
            "var o={};for(var i=0;i<localStorage.length;i++){"
            "var k=localStorage.key(i);o[k]=localStorage.getItem(k);}return JSON.stringify(o);"
        )
        import json as _json
        data = _json.loads(raw) if isinstance(raw, str) else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_local_storage(tab, storage: dict) -> int:
    """把已保存的 localStorage 写回页面，返回写入条数。"""
    n = 0
    for k, v in (storage or {}).items():
        if v is None:
            continue
        try:
            tab.run_js("localStorage.setItem(arguments[0], arguments[1]);", k, str(v))
            n += 1
        except Exception:
            continue
    return n


def _storage_json_path(platform: str) -> str:
    return os.path.join(COOKIE_DIR, f"{platform}_storage.json")


# 各平台需要持久化的 localStorage 登录相关键
_STORAGE_PERSIST_KEYS = {
    "goofishpro": [
        "access_token", "user_token", "seller_id", "account_id",
        "seller_mobile", "mobile", "my_key", "my_iv", "permission",
        "seller_name", "username", "shop_name",
    ],
}


def _persist_storage(tab, platform: str) -> int:
    """读取并保存平台登录相关的 localStorage 键，返回保存条数。"""
    keys = _STORAGE_PERSIST_KEYS.get(platform)
    store = _read_local_storage(tab)
    if not store:
        return 0
    if keys:
        store = {k: store[k] for k in keys if k in store}
    if not store:
        return 0
    save_storage(platform, store)
    return len(store)


def save_storage(platform: str, storage: dict) -> None:
    """保存 localStorage 到本地 JSON（仅保存登录相关键，避免噪声）。"""
    _ensure_dirs()
    try:
        with open(_storage_json_path(platform), "w", encoding="utf-8") as f:
            import json as _json
            _json.dump({"platform": platform, "saved_at": datetime.now().isoformat(),
                        "storage": storage}, f, ensure_ascii=False)
    except Exception:
        pass


def load_storage(platform: str) -> dict:
    path = _storage_json_path(platform)
    if not os.path.exists(path):
        return {}
    try:
        import json as _json
        data = _json.load(open(path, encoding="utf-8"))
        return data.get("storage", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


# ── 登录判定 ─────────────────────────────────────────────────
def _on_login_page(url: str, platform: str) -> bool:
    parts = _rule(platform).get("login_url_parts", [])
    low = (url or "").lower()
    return any(p.lower() in low for p in parts)


def is_logged_in(tab, platform: str) -> bool:
    """通用登录判定：不在登录页 + 存在登录态 Cookie。"""
    try:
        url = tab.url or ""
    except Exception:
        url = ""
    if _on_login_page(url, platform):
        return False

    rule = _rule(platform)
    cookie_keys = rule.get("cookie_keys") or _GENERIC_COOKIE_KEYS
    try:
        names = set()
        for c in _read_tab_cookies(tab):
            name = c.get("name") or c.get("Name") or ""
            if name:
                names.add(name)
        if any(k in names for k in cookie_keys):
            return True
    except Exception:
        pass

    # SPA 平台：登录态存在 localStorage（如闲管家的 access_token）
    storage_keys = rule.get("storage_keys") or []
    if storage_keys:
        try:
            store = _read_local_storage(tab)
            for k in storage_keys:
                val = store.get(k)
                if val and str(val).lower() not in ("", "undefined", "null", "false", "0"):
                    return True
        except Exception:
            pass

    # 暂未确定凭证名的平台：离开登录页 + 存在任意 Cookie 即视为已登录
    try:
        if rule.get("off_login_page_ok") and names:
            return True
    except Exception:
        pass
    return False


# ── 核心入口 ─────────────────────────────────────────────────
def ensure_login(
    platform: str,
    on_log: Callable[[str], None] | None = None,
    timeout: int = 600,
    headless: bool = False,
    reuse_browser=None,
):
    """确保某平台已登录，必要时打开浏览器等待用户登录。

    返回 dict：
        {"ok": bool, "platform": str, "cookies": list, "tab": tab, "browser": browser,
         "error": str}

    - 若已有有效 Cookie / profile 登录态 → 直接成功。
    - 否则打开登录页，每秒轮询，直到检测到登录成功（最长 timeout 秒），
      成功后自动提取并保存 Cookie。
    - 调用方负责在用完后关闭 browser（除非传入 reuse_browser）。
    """
    log = on_log or (lambda m: None)

    ok, msg = check_browser_available()
    if not ok:
        return {"ok": False, "platform": platform, "error": f"浏览器不可用: {msg}",
                "cookies": [], "tab": None, "browser": None}

    home = _platform_url(platform, "home")
    login_url = _platform_url(platform, "login") or home
    if not home and not login_url:
        return {"ok": False, "platform": platform, "error": f"未知平台或缺少 URL: {platform}",
                "cookies": [], "tab": None, "browser": None}

    from DrissionPage import Chromium

    browser = reuse_browser
    if browser is None:
        profile = _profile_dir(platform)
        os.makedirs(profile, exist_ok=True)
        co, _ = get_chromium_options(user_data_dir=profile, headless=headless)
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-blink-features=AutomationControlled")
        browser = Chromium(co)

    tab = browser.latest_tab

    # 1) 打开首页，注入已存 Cookie，看是否已登录
    log(f"检查 {platform} 登录状态…")
    try:
        tab.get(home or login_url)
    except Exception as e:
        return {"ok": False, "platform": platform, "error": f"打开页面失败: {e}",
                "cookies": [], "tab": tab, "browser": browser}
    time.sleep(2)

    saved = load_cookies(platform)
    saved_store = load_storage(platform)
    if saved or saved_store:
        injected = _inject_cookies(tab, saved) if saved else 0
        wrote = _write_local_storage(tab, saved_store) if saved_store else 0
        if injected or wrote:
            log(f"已注入 {injected} 条 Cookie / {wrote} 条 localStorage，刷新校验…")
            try:
                tab.get(home or login_url)
            except Exception:
                pass
            time.sleep(2)

    if is_logged_in(tab, platform):
        cookies = _read_tab_cookies(tab)
        if cookies:
            save_cookies(platform, cookies, extra={"profile": _profile_dir(platform)})
        _persist_storage(tab, platform)
        log(f"✅ {platform} 已登录（使用已保存的登录态）")
        return {"ok": True, "platform": platform, "cookies": cookies,
                "tab": tab, "browser": browser, "error": ""}

    # 2) 未登录 → 打开登录页，等待用户登录
    log("=" * 48)
    log(f"⚠️  {platform} 未登录。请在弹出的浏览器里完成登录。")
    log("登录成功后程序会自动继续，无需手动操作。")
    log(f"（最长等待 {timeout} 秒；登录态会被保存，下次免登录）")
    log("=" * 48)
    try:
        tab.get(login_url)
    except Exception:
        pass

    waited = 0
    while waited < timeout:
        time.sleep(2)
        waited += 2
        if is_logged_in(tab, platform):
            cookies = _read_tab_cookies(tab)
            save_cookies(platform, cookies, extra={"profile": _profile_dir(platform)})
            saved_keys = _persist_storage(tab, platform)
            log(f"✅ {platform} 登录成功，已保存 {len(cookies)} 条 Cookie / {saved_keys} 条 localStorage。")
            return {"ok": True, "platform": platform, "cookies": cookies,
                    "tab": tab, "browser": browser, "error": ""}
        if waited % 30 == 0:
            log(f"  ⏳ 等待登录… ({waited}s/{timeout}s)")

    return {"ok": False, "platform": platform, "error": "登录等待超时",
            "cookies": _read_tab_cookies(tab), "tab": tab, "browser": browser}
