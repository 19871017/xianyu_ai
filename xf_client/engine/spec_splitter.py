"""按主规格拆单：把双规格轴商品拆成多个单规格轴子商品（纯逻辑，可单测）。

背景：闲鱼多规格按「轴1×轴2」笛卡尔积生成所有行且不可删行，每行强制要价格+
库存。采集源往往只覆盖部分组合（如某色不含某机型），缺失组合只能填库存 0
占位，会在前台显示「无货」规格。

本模块提供另一条更干净的路径：按取值较少的轴（如颜色）分组，拆成多个单轴
子商品（每个子商品的次轴只含该主规格下真实存在的值），从根本上消除空缺组合，
前台不再出现无货占位规格，也更利于按机型/款式搜索。

设计：split_by_primary_spec 为纯函数，不碰浏览器/网络/数据库，便于单测与复用。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any


TITLE_SPEC_MAXLEN = 20  # 拼到标题里的主规格值最大长度


def _s(value: Any) -> str:
    return str(value or "").strip()


def _distinct(sku_list: list[dict[str, Any]], key: str) -> list[str]:
    """按出现顺序取某规格轴的去重取值（非空）。"""
    seen: list[str] = []
    for sku in sku_list:
        v = _s(sku.get(key))
        if v and v not in seen:
            seen.append(v)
    return seen


def _secondary_attrs(attrs: Any, primary_value: str) -> dict[str, Any]:
    """从原 sku_attrs 中剔除「值等于主规格值」的项，保留次规格属性。

    按值匹配而非按 key 顺序，鲁棒于采集器写入顺序与 spec1/spec2 不一致的情况。
    """
    if not isinstance(attrs, dict) or not attrs:
        return {}
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if _s(v) == primary_value:
            continue
        out[k] = v
    return out


def _compose_title(base_title: str, spec_value: str) -> str:
    """把主规格值拼到标题尾部，避免重复与过长。"""
    base = _s(base_title)
    sv = _s(spec_value)[:TITLE_SPEC_MAXLEN]
    if not sv:
        return base
    if sv in base:
        return base
    return f"{base} {sv}".strip()


def _min_price(sku_list: list[dict[str, Any]]) -> float:
    prices = []
    for s in sku_list:
        try:
            p = float(s.get("price") or 0)
        except Exception:
            p = 0.0
        if p > 0:
            prices.append(p)
    return min(prices) if prices else 0.0


def count_cartesian_gaps(item: dict[str, Any]) -> dict[str, int]:
    """统计双轴商品的笛卡尔积空缺数：返回 {axis1, axis2, real, cartesian, gaps}。

    供 UI 提示拆单收益（gaps 即拆单后可消除的无货占位数）。非双轴返回全 0。
    """
    sku_list = item.get("sku_list") or []
    d1 = _distinct(sku_list, "spec1")
    d2 = _distinct(sku_list, "spec2")
    if not d1 or not d2:
        return {"axis1": len(d1), "axis2": len(d2), "real": len(sku_list),
                "cartesian": 0, "gaps": 0}
    cartesian = len(d1) * len(d2)
    real = len({(_s(s.get("spec1")), _s(s.get("spec2"))) for s in sku_list
                if _s(s.get("spec1")) or _s(s.get("spec2"))})
    return {"axis1": len(d1), "axis2": len(d2), "real": real,
            "cartesian": cartesian, "gaps": max(0, cartesian - real)}


def split_by_primary_spec(
    item: dict[str, Any],
    split_axis: str = "auto",
    max_children: int = 50,
) -> list[dict[str, Any]]:
    """把双规格轴商品按主规格轴拆成多个单轴子商品。

    Args:
        item: 商品包（含 sku_list，每项有 spec1/spec2）。
        split_axis: "spec1" | "spec2" | "auto"。auto 选取值较少的轴作为拆分轴
            （子商品数 = 该轴取值数，更少的清单项）。
        max_children: 子商品数量上限，超出则截断（防极端类目爆量）。

    Returns:
        子商品列表。非双轴（无 spec2 或 SKU<2）原样返回 [item 副本]。
        子商品：清空 db_id/xianyu_item_id、状态置 collected、标题带主规格、
        次轴提升为单轴 spec1、价格回填该组最低价、首图优先用该组规格图。
    """
    base = dict(item)
    sku_list = base.get("sku_list") or []
    has_spec2 = any(_s(s.get("spec2")) for s in sku_list)
    if not has_spec2 or len(sku_list) < 2:
        return [base]

    d1 = _distinct(sku_list, "spec1")
    d2 = _distinct(sku_list, "spec2")
    if not d1 or not d2:
        return [base]

    if split_axis == "spec1":
        prim, sec = "spec1", "spec2"
    elif split_axis == "spec2":
        prim, sec = "spec2", "spec1"
    else:  # auto：取值较少的轴作为拆分轴，子商品更少。
        prim, sec = ("spec1", "spec2") if len(d1) <= len(d2) else ("spec2", "spec1")

    groups: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for sku in sku_list:
        pv = _s(sku.get(prim))
        if not pv:
            continue
        groups.setdefault(pv, []).append(sku)

    base_title = base.get("title") or base.get("original_title") or ""
    parent_id = base.get("db_id") or base.get("item_id") or ""

    children: list[dict[str, Any]] = []
    for pv, skus in groups.items():
        child = dict(base)
        child.pop("db_id", None)
        child["xianyu_item_id"] = ""
        child["status"] = "collected"
        child["split_from"] = parent_id
        child["split_spec_value"] = pv
        child["title"] = _compose_title(base_title, pv)
        child["original_title"] = child["title"]

        new_skus: list[dict[str, Any]] = []
        for sku in skus:
            ns = dict(sku)
            sec_val = _s(sku.get(sec))
            ns["spec1"] = sec_val or "默认"
            ns["spec2"] = ""
            ns["sku_attrs"] = _secondary_attrs(sku.get("sku_attrs"), pv)
            new_skus.append(ns)
        child["sku_list"] = new_skus

        mp = _min_price(new_skus)
        if mp > 0:
            child["price"] = mp
            child["new_price"] = f"{mp:.2f}"

        # 首图优先用该组任一规格图（让清单缩略图匹配该主规格，如该颜色）。
        rep_img = next((_s(s.get("sku_image")) for s in skus if _s(s.get("sku_image"))), "")
        if rep_img:
            mains = [m for m in (child.get("main_images") or []) if m]
            child["main_images"] = [rep_img] + [m for m in mains if m != rep_img]

        children.append(child)
        if len(children) >= max_children:
            break

    return children
