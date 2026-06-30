"""新订单语音/系统提醒：跨平台（macOS / Windows），纯标准库实现。

设计：
  - speak(text)：朗读一句中文提醒。mac 用内置 ``say``，win 用 SAPI(PowerShell)，
    都不可用时静默降级（不抛异常，不阻塞 UI）。
  - notify(title, message)：弹系统通知（mac osascript / win toast 兜底气泡）。
  - 朗读在子线程执行，绝不阻塞调用方（抓单完成回调在 UI 线程）。
  - 偏好（是否开启语音）存于 ``~/.xf_notify.json``，与 AI 配置文件互不覆盖。
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading

_PREF_PATH = os.path.join(os.path.expanduser("~"), ".xf_notify.json")
_DEFAULT_PREF = {"voice_enabled": True}


def load_pref() -> dict:
    """读取提醒偏好；文件缺失/损坏时返回默认值。"""
    pref = dict(_DEFAULT_PREF)
    try:
        if os.path.exists(_PREF_PATH):
            with open(_PREF_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                pref.update({k: data[k] for k in _DEFAULT_PREF if k in data})
    except Exception:
        pass
    return pref


def save_pref(pref: dict) -> None:
    """写回提醒偏好（只保留已知键）。"""
    data = {k: pref.get(k, _DEFAULT_PREF[k]) for k in _DEFAULT_PREF}
    try:
        with open(_PREF_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def is_voice_enabled() -> bool:
    return bool(load_pref().get("voice_enabled", True))


def set_voice_enabled(enabled: bool) -> None:
    pref = load_pref()
    pref["voice_enabled"] = bool(enabled)
    save_pref(pref)


def _speak_blocking(text: str) -> bool:
    """同步朗读一段文本，成功返回 True。无可用引擎返回 False。"""
    system = platform.system()
    try:
        if system == "Darwin":
            say = shutil.which("say")
            if say:
                subprocess.run([say, text], check=False,
                               timeout=30)
                return True
        elif system == "Windows":
            ps = shutil.which("powershell") or shutil.which("powershell.exe")
            if ps:
                # 经 SAPI 朗读；文本以参数传入避免拼接注入。
                script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$s.Speak($args[0])"
                )
                subprocess.run([ps, "-NoProfile", "-NonInteractive",
                                "-Command", script, text],
                               check=False, timeout=30)
                return True
    except Exception:
        pass
    return False


def speak(text: str) -> None:
    """异步朗读，不阻塞调用线程。语音被关闭时直接跳过。"""
    if not text or not is_voice_enabled():
        return
    threading.Thread(target=_speak_blocking, args=(text,),
                     daemon=True).start()


def _notify_blocking(title: str, message: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            osa = shutil.which("osascript")
            if osa:
                # 用参数传入文本，避免引号/注入问题。
                script = 'display notification (item 1 of argv) with title (item 2 of argv)'
                subprocess.run([osa, "-e", script, message, title],
                               check=False, timeout=10)
                return
        elif system == "Windows":
            ps = shutil.which("powershell") or shutil.which("powershell.exe")
            if ps:
                script = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
                    "$n = New-Object System.Windows.Forms.NotifyIcon; "
                    "$n.Icon = [System.Drawing.SystemIcons]::Information; "
                    "$n.Visible = $true; "
                    "$n.ShowBalloonTip(8000, $args[0], $args[1], "
                    "[System.Windows.Forms.ToolTipIcon]::Info)"
                )
                subprocess.run([ps, "-NoProfile", "-NonInteractive",
                                "-Command", script, title, message],
                               check=False, timeout=10)
                return
    except Exception:
        pass


def notify(title: str, message: str) -> None:
    """异步弹系统通知，不阻塞调用线程。"""
    if not (title or message):
        return
    threading.Thread(target=_notify_blocking, args=(title, message),
                     daemon=True).start()


def alert_new_orders(count: int) -> None:
    """新订单到达时的组合提醒：系统通知 + 语音播报。

    count: 本次新增订单数。<=0 不提醒。
    """
    if count <= 0:
        return
    title = "闲鱼新订单"
    message = f"您有 {count} 个新订单，请注意查看"
    notify(title, message)
    speak(f"您有{count}个新订单了，请注意查看")


# ──────────────────────── 新订单识别 ────────────────────────

_SEEN_PATH = os.path.join(os.path.expanduser("~"), ".xf_seen_orders.json")
_SEEN_MAX = 2000  # 防止文件无限膨胀，只保留最近这么多个 key


def _order_key(order: dict) -> str:
    """为一条订单生成稳定标识：优先平台订单号，否则多字段哈希。

    闲鱼已售页常抽不到真正订单号，故用 商品id+规格+金额+买家+标题 兜底，
    足以区分不同订单、又能在多次抓取间保持稳定。
    """
    order = order or {}
    poid = str(order.get("platform_order_id") or "").strip()
    if poid:
        return "id:" + poid
    parts = [
        str(order.get("xianyu_item_id") or order.get("source_item_id") or ""),
        str(order.get("buyer_spec") or ""),
        str(order.get("order_amount") or ""),
        str(order.get("buyer_name") or ""),
        str(order.get("title") or ""),
    ]
    raw = "|".join(parts)
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_seen() -> list:
    try:
        if os.path.exists(_SEEN_PATH):
            with open(_SEEN_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data]
    except Exception:
        pass
    return []


def _save_seen(keys: list) -> None:
    try:
        trimmed = keys[-_SEEN_MAX:]
        with open(_SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False)
    except Exception:
        pass


def detect_new_orders(orders: list) -> int:
    """对比已见订单集合，返回本次新增订单数，并把当前订单并入集合。

    首次使用（无记录文件）建立基线：把当前订单全部记为已见但不算新增，
    避免初次抓单就报一大堆“新订单”。
    """
    orders = orders or []
    keys = [_order_key(o) for o in orders if isinstance(o, dict)]
    seen = _load_seen()
    seen_set = set(seen)
    first_time = not os.path.exists(_SEEN_PATH)
    new_count = 0 if first_time else sum(1 for k in keys if k not in seen_set)
    # 合并去重，保持顺序：旧的在前、新出现的在后。
    merged = list(seen)
    for k in keys:
        if k not in seen_set:
            seen_set.add(k)
            merged.append(k)
    _save_seen(merged)
    return new_count
