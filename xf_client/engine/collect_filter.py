"""采集结果筛选与排序（纯函数，无浏览器依赖，便于单元测试）。

采集到的每个商品 item 至少含：
  price (float)、wants/views/collects (str，可能含 "万"/"+" 等)、sales (可选)。

本模块把这些字段统一成数值，并按用户在采集页选择的条件过滤 + 排序：
  - 价格区间 min_price / max_price
  - 最低销量 min_sales（拼多多等用 sales，其余用 wants 近似“热度/想要”）
  - 最低想要 min_wants、最低浏览 min_views
  - 排序 sort_by ∈ {price, sales, wants, views}，order ∈ {asc, desc}
"""

from __future__ import annotations

import re
from typing import Any


def parse_number(value: Any) -> float:
    """把 "1.2万" / "3000+" / "已拼5万件" / "¥9.9" 等解析成数值。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace(",", "").replace("，", "")
    # 中文万/亿单位
    m = re.search(r"(\d+(?:\.\d+)?)\s*亿", text)
    if m:
        return float(m.group(1)) * 1e8
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if m:
        return float(m.group(1)) * 1e4
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        return float(m.group(0))
    return 0.0


# 各排序键 → 从 item 取值的函数
_SORT_KEYS = {
    "price": lambda it: parse_number(it.get("price") or it.get("original_price")),
    "sales": lambda it: parse_number(it.get("sales") or it.get("wants")),
    "wants": lambda it: parse_number(it.get("wants")),
    "views": lambda it: parse_number(it.get("views")),
}


def item_sales(item: dict[str, Any]) -> float:
    """商品销量：优先 sales 字段，回退 wants（闲鱼“想要”近似销量）。"""
    return parse_number(item.get("sales") or item.get("wants"))


def filter_items(
    items: list[dict[str, Any]],
    min_price: float | None = None,
    max_price: float | None = None,
    min_sales: float | None = None,
    min_wants: float | None = None,
    min_views: float | None = None,
    sort_by: str | None = None,
    order: str = "desc",
) -> list[dict[str, Any]]:
    """按条件过滤并排序商品列表。返回新列表，不修改入参。

    所有阈值为 None 时不启用对应过滤。无效/缺失字段按 0 处理。
    """
    result: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        price = parse_number(item.get("price") or item.get("original_price"))
        sales = item_sales(item)
        wants = parse_number(item.get("wants"))
        views = parse_number(item.get("views"))

        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        if min_sales is not None and sales < min_sales:
            continue
        if min_wants is not None and wants < min_wants:
            continue
        if min_views is not None and views < min_views:
            continue
        result.append(item)

    if sort_by and sort_by in _SORT_KEYS:
        keyfn = _SORT_KEYS[sort_by]
        result.sort(key=keyfn, reverse=(order != "asc"))

    return result
