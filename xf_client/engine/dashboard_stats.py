"""经营概览统计：基于本地数据库的商品/订单/复检数据，汇总经营指标。

设计：
  - compute_dashboard 为纯逻辑函数（不依赖浏览器/网络），便于单测与复用。
  - 数据全部来自本地库（已采集/已上架商品、已抓取订单、复检结果），
    100% 可靠、即时可用；线上实时抓取作为后续可选增强，不在本模块。

指标维度：
  商品：总数、按状态（待处理/已上架闲鱼/已上架闲管家）、按来源平台、多规格占比、
        关注/浏览合计（采集时快照）。
  订单：总数、按状态、成交额合计、源头匹配率（可回源补货比例）。
  利润：已上架商品的加价率分布、潜在毛利（闲鱼售价−源价）合计与均值。
  风险：最近复检的严重/警告商品数（来自 source_recheck 落库结果）。
"""
from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except Exception:
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return 0


# 上架状态 → 是否已上架。
_LISTED_STATUSES = {"listed_xianyu", "listed_goofishpro"}

_PLATFORM_LABELS = {
    "xianyu": "闲鱼", "pdd": "拼多多", "jd": "京东",
    "1688": "1688", "taobao": "淘宝/天猫", "goofishpro": "闲管家",
}

_STATUS_LABELS = {
    "collected": "待处理",
    "listed_xianyu": "已上架闲鱼",
    "listed_goofishpro": "已上架闲管家",
}


def compute_dashboard(
    products: list[dict[str, Any]] | None = None,
    orders: list[dict[str, Any]] | None = None,
    rechecks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """汇总经营概览指标。所有入参均为本地库记录列表。"""
    products = products or []
    orders = orders or []
    rechecks = rechecks or []

    # ── 商品维度 ──
    by_status: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    multi_sku_cnt = 0
    listed_cnt = 0
    total_wants = 0
    total_views = 0
    markups: list[float] = []      # 加价率（%）
    gross_margins: list[float] = []  # 单品潜在毛利（闲鱼售价 − 源价）

    for p in products:
        status = p.get("status") or "collected"
        by_status[status] = by_status.get(status, 0) + 1

        platform = p.get("platform") or p.get("source_platform") or "unknown"
        by_platform[platform] = by_platform.get(platform, 0) + 1

        sku_list = p.get("sku_list") or []
        if len(sku_list) > 1:
            multi_sku_cnt += 1

        total_wants += _to_int(p.get("wants"))
        total_views += _to_int(p.get("views"))

        is_listed = status in _LISTED_STATUSES
        if is_listed:
            listed_cnt += 1

        # 利润：源价 original_price，闲鱼售价 new_price。
        src = _to_float(p.get("original_price"))
        sell = _to_float(p.get("new_price"))
        if is_listed and src > 0 and sell > 0:
            markups.append((sell - src) / src * 100.0)
            gross_margins.append(sell - src)

    # ── 订单维度 ──
    order_total = len(orders)
    order_by_status: dict[str, int] = {}
    revenue = 0.0
    matched_cnt = 0
    for o in orders:
        ost = o.get("order_status") or "pending"
        order_by_status[ost] = order_by_status.get(ost, 0) + 1
        revenue += _to_float(o.get("order_amount"))
        if (o.get("match_status") or "unmatched") == "matched":
            matched_cnt += 1
    match_rate = (matched_cnt / order_total * 100.0) if order_total else 0.0

    # ── 风险维度（来自复检落库结果）──
    risk_critical = sum(1 for r in rechecks if r.get("level") == "critical")
    risk_warn = sum(1 for r in rechecks if r.get("level") == "warn")

    avg_markup = round(sum(markups) / len(markups), 1) if markups else 0.0
    total_margin = round(sum(gross_margins), 2) if gross_margins else 0.0
    avg_margin = round(sum(gross_margins) / len(gross_margins), 2) if gross_margins else 0.0

    return {
        "products": {
            "total": len(products),
            "listed": listed_cnt,
            "multi_sku": multi_sku_cnt,
            "total_wants": total_wants,
            "total_views": total_views,
            "by_status": _labelize(by_status, _STATUS_LABELS),
            "by_platform": _labelize(by_platform, _PLATFORM_LABELS),
        },
        "orders": {
            "total": order_total,
            "revenue": round(revenue, 2),
            "matched": matched_cnt,
            "match_rate": round(match_rate, 1),
            "by_status": order_by_status,
        },
        "profit": {
            "avg_markup_pct": avg_markup,
            "total_gross_margin": total_margin,
            "avg_gross_margin": avg_margin,
            "sample": len(markups),
        },
        "risk": {
            "critical": risk_critical,
            "warn": risk_warn,
        },
    }


def _labelize(counts: dict[str, int], labels: dict[str, str]) -> list[dict[str, Any]]:
    """把 {key:count} 转成带中文标签、按数量降序的列表，便于 UI 展示。"""
    out = [
        {"key": k, "label": labels.get(k, k), "count": v}
        for k, v in counts.items()
    ]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out
