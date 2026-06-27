"""京东商品多规格(SKU)解析。

京东详情页把所有规格组合放在运行时 JS 变量
``pageConfig.product.colorSize``（数组），每项形如::

    {"skuId": "100012043978", "颜色": "飞天53度", "尺码": "500ml",
     "Color": "...", "Size": "..."}

每项含 ``skuId`` 与若干规格维度键（颜色/尺码/版本/套餐等，维度名因品类而异，
可能同时含英文重复键 Color/Size）。本模块把它解析为统一 sku_list：
``spec1/spec2/price/stock/merchant_sku/source_sku_id/sku_attrs``。

价格：京东各 SKU 单价需独立接口（``p.3.cn``，对非浏览器直连会被风控重置），
采集时拿不到稳定的逐 SKU 价，故价格回退到商品主价（由上层填入 ``base_price``）。
"""

from __future__ import annotations

import json
import re
from typing import Any


# 始终排除的键：skuId 自身。
_SKU_ID_KEYS = {"skuid"}


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except Exception:
        return False


def _spec_dimension_keys(color_size: list[dict[str, Any]]) -> list[str]:
    """按出现顺序收集规格维度键，排除 skuId。

    京东常给中文维度（颜色/尺码/版本…）同时附带英文重复键（Color/Size）。
    若存在中文维度，英文键视为重复予以丢弃；只有英文维度时再用英文，避免漏采。
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in color_size:
        if not isinstance(entry, dict):
            continue
        for key in entry.keys():
            k = str(key)
            if k.strip().lower() in _SKU_ID_KEYS:
                continue
            if k in seen:
                continue
            seen.add(k)
            ordered.append(k)
    zh = [k for k in ordered if not _is_ascii(k)]
    en = [k for k in ordered if _is_ascii(k)]
    # 有中文维度则只用中文（英文为重复）；否则回退英文维度。
    return (zh[:2] if zh else en[:2])


def parse_jd_sku_list(
    color_size: Any, base_price: float = 0.0, base_stock: int = 1000
) -> list[dict[str, Any]]:
    """把京东 colorSize 数组解析为统一 sku_list。

    Args:
        color_size: pageConfig.product.colorSize（list[dict] 或其 JSON 字符串）。
        base_price: 逐 SKU 价拿不到时的回退价（商品主价）。
        base_stock: 库存占位（京东不在 colorSize 暴露库存）。

    Returns:
        sku_list；解析不到返回空列表。
    """
    if isinstance(color_size, str):
        try:
            color_size = json.loads(color_size)
        except Exception:
            return []
    if not isinstance(color_size, list) or not color_size:
        return []

    dim_keys = _spec_dimension_keys(color_size)
    if not dim_keys:
        return []

    sku_list: list[dict[str, Any]] = []
    seen_combo: set[tuple] = set()
    for entry in color_size:
        if not isinstance(entry, dict):
            continue
        sku_id = str(entry.get("skuId") or entry.get("skuid") or "").strip()
        spec_vals: list[str] = []
        sku_attrs: dict[str, str] = {}
        for key in dim_keys:
            val = str(entry.get(key) or "").strip()
            if val:
                spec_vals.append(val)
                sku_attrs[str(key)] = val
        spec1 = spec_vals[0] if spec_vals else "默认"
        spec2 = spec_vals[1] if len(spec_vals) > 1 else ""

        combo = (spec1, spec2, sku_id)
        if combo in seen_combo:
            continue
        seen_combo.add(combo)

        sku_list.append({
            "spec1": spec1,
            "spec2": spec2,
            "price": base_price,
            "stock": base_stock,
            "sku_image_url": "",
            "merchant_sku": sku_id,
            "source_sku_id": sku_id,
            "sku_attrs": sku_attrs,
        })
    return sku_list


def extract_color_size_from_html(html: str) -> list[dict[str, Any]]:
    """从详情页 HTML 里抠出 colorSize 数组（run_js 拿不到时的兜底）。"""
    if not html or not isinstance(html, str):
        return []
    m = re.search(r"colorSize\s*:\s*(\[.*?\])\s*[,}]", html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, list) else []
    except Exception:
        return []
