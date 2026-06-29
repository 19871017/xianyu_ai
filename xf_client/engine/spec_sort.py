"""规格值排序：把 iPhone 等机型规格按标准代际/变体顺序排列（纯逻辑，可单测）。

背景：1688/淘宝采集到的机型规格顺序常是乱的（如 12→13→16→14），直接上架
到闲鱼买家体验差。本模块识别 iPhone 机型并按「代际升序 + 同代变体顺序」重排，
非机型规格保持原顺序（稳定排序，不破坏其它品类）。

设计：sort_skus_by_spec / iphone_model_sort_key 为纯函数，不碰浏览器/网络/库。
"""
from __future__ import annotations

import re
from typing import Any


def _variant_rank(text: str) -> int:
    """从机型文本判断变体等级：标准=0, mini=1, pro=2, plus=3, max=4, promax=5。"""
    t = text.replace(" ", "").replace("（", "(").lower()
    # promax/pro max 要先于 pro/max 判断，避免被子串误命中。
    if "promax" in t or "pro_max" in t:
        return 5
    if "plus" in t:
        return 3
    if "pro" in t:
        return 2
    if "max" in t:
        return 4
    if "mini" in t:
        return 1
    return 0


def iphone_model_sort_key(value: Any) -> tuple:
    """生成 iPhone 机型排序键：(主版本号, 变体等级, 原文)。

    无法识别版本号时主版本号置很大（排到末尾），保证机型在前、杂项在后。
    """
    s = str(value or "").strip().lower()
    # 抓第一个 1~2 位主版本号（iPhone 11~17、X 系列等用 0 占位靠前的不在此列）。
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", s)
    major = int(m.group(1)) if m else 999
    return (major, _variant_rank(s), s)


def is_iphone_model_axis(values: list[str], min_ratio: float = 0.6) -> bool:
    """判断一组规格值是否主要是 iPhone 机型（命中比例 >= min_ratio）。"""
    vals = [str(v or "").strip().lower() for v in values if str(v or "").strip()]
    if not vals:
        return False
    hit = 0
    for v in vals:
        if "iphone" in v or "苹果" in v or "promax" in v or "pro max" in v \
                or re.search(r"(?<!\d)1[1-7](?!\d)\s*(pro|plus|max|mini|promax)?", v):
            hit += 1
    return hit / len(vals) >= min_ratio


def sort_skus_by_spec(sku_list: list[dict[str, Any]], spec_key: str = "spec1") -> list[dict[str, Any]]:
    """若 spec_key 轴主要是 iPhone 机型，按标准顺序重排 SKU；否则原样返回。

    稳定排序：同键值保持原相对顺序，不影响非机型品类。
    """
    skus = list(sku_list or [])
    values = [s.get(spec_key) for s in skus]
    if not is_iphone_model_axis([str(v) for v in values]):
        return skus
    return sorted(skus, key=lambda s: iphone_model_sort_key(s.get(spec_key)))
