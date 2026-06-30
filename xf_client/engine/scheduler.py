"""任务调度核心：下次运行时间计算与到期判断（纯逻辑，可单测）。

设计：
  - compute_next_run / is_due 为纯函数，不依赖线程/定时器/网络，便于单测。
  - 支持两种触发方式：
      interval — 每 N 分钟运行一次（N>=1）。
      daily    — 每天在 HH:MM 运行一次。
  - 调度的「执行」由 UI 层的 QTimer 周期性调用 due_tasks 后分发，
    具体任务（源复检/采集）复用已实测的引擎，调度本身不碰浏览器。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


VALID_TRIGGERS = ("interval", "daily")
VALID_TASK_TYPES = ("recheck", "collect", "polish", "fetch_orders")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _parse_hhmm(value: Any) -> tuple[int, int]:
    """解析 'HH:MM' → (hour, minute)，非法回退 (9, 0)。"""
    try:
        hh, mm = str(value).strip().split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return 9, 0


def compute_next_run(task: dict[str, Any], now: datetime | None = None) -> datetime:
    """计算任务的下次运行时间。

    Args:
        task: {"trigger","interval_minutes"/"daily_time","last_run"(可选)}
        now:  当前时间（默认 datetime.now()，测试可注入）。

    Returns:
        下次应运行的 datetime（始终 > now，除非从未运行的 interval 任务立即到期）。
    """
    now = now or datetime.now()
    trigger = task.get("trigger", "interval")

    if trigger == "daily":
        h, m = _parse_hhmm(task.get("daily_time", "09:00"))
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # interval：基于 last_run + 间隔；从未运行则下次=now（立即到期）。
    minutes = max(1, int(task.get("interval_minutes") or 60))
    last = _parse_dt(task.get("last_run"))
    if last is None:
        return now
    nxt = last + timedelta(minutes=minutes)
    # 若已错过多个周期，对齐到「下一个未来时刻」，避免补跑堆积。
    if nxt <= now:
        return now
    return nxt


def is_due(task: dict[str, Any], now: datetime | None = None) -> bool:
    """判断任务此刻是否到期应运行（启用且 next_run <= now）。"""
    now = now or datetime.now()
    if not task.get("enabled", True):
        return False
    nxt = compute_next_run(task, now)
    return nxt <= now


def due_tasks(tasks: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """从任务列表中筛出此刻到期的（启用的）任务。"""
    now = now or datetime.now()
    return [t for t in (tasks or []) if is_due(t, now)]


def validate_task(task: dict[str, Any]) -> dict[str, Any]:
    """校验任务配置，返回 {ok, reasons}。"""
    reasons: list[str] = []
    if task.get("task_type") not in VALID_TASK_TYPES:
        reasons.append(f"task_type 须为 {VALID_TASK_TYPES}")
    if task.get("trigger") not in VALID_TRIGGERS:
        reasons.append(f"trigger 须为 {VALID_TRIGGERS}")
    if task.get("trigger") == "interval":
        try:
            if int(task.get("interval_minutes") or 0) < 1:
                reasons.append("interval_minutes 须 >= 1")
        except Exception:
            reasons.append("interval_minutes 须为整数")
    if task.get("trigger") == "daily":
        h, m = _parse_hhmm(task.get("daily_time", ""))
        if (h, m) == (9, 0) and str(task.get("daily_time", "")).strip() not in ("09:00", "9:00", ""):
            reasons.append("daily_time 格式须为 HH:MM")
    return {"ok": not reasons, "reasons": reasons}


def describe_schedule(task: dict[str, Any]) -> str:
    """生成人类可读的调度描述，用于 UI 展示。"""
    trigger = task.get("trigger", "interval")
    if trigger == "daily":
        h, m = _parse_hhmm(task.get("daily_time", "09:00"))
        return f"每天 {h:02d}:{m:02d}"
    minutes = max(1, int(task.get("interval_minutes") or 60))
    if minutes % 60 == 0:
        return f"每 {minutes // 60} 小时"
    return f"每 {minutes} 分钟"
