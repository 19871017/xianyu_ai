"""利润测算 + 选品打分（纯逻辑，可单测）。

用途：采集后决策「哪些商品值得上架闲鱼」。两块能力：
  1. compute_profit —— 单品净利润/净利率测算。
     成本 = 源价 + 运费 + 其它成本；平台费 = 售价 × 费率；
     净利润 = 售价 − 成本 − 平台费。闲鱼个人卖家近乎 C2C 免佣，
     平台费率默认很低（可配）；运费因 gross_weight_kg 普遍缺失，
     用可配默认值而非依赖重量，保证可落地。
  2. score_product —— 综合净利率/需求热度/加价空间/多规格/库存打分(0-100)，
     给出推荐等级与理由。缺失信号给中性分，不过度惩罚。

设计：全部为纯函数，不碰浏览器/网络/数据库，便于单测与复用。
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


# 默认参数（可被 UI 覆盖）。闲鱼个人卖家近乎免佣，给保守的综合费率。
DEFAULT_SHIPPING_COST = 5.0      # 卖家承担的默认运费（元）
DEFAULT_PLATFORM_FEE_PCT = 0.6   # 平台综合费率（%）：提现/服务等
DEFAULT_EXTRA_COST = 0.0         # 其它单品成本（包装等）


def compute_profit(
    source_price: float,
    sell_price: float,
    *,
    shipping_cost: float = DEFAULT_SHIPPING_COST,
    platform_fee_pct: float = DEFAULT_PLATFORM_FEE_PCT,
    extra_cost: float = DEFAULT_EXTRA_COST,
) -> dict[str, Any]:
    """单品利润测算。

    Returns:
        {cost, platform_fee, net_profit, net_margin_pct, markup_pct, profitable}
        cost 含源价+运费+其它成本；net_margin_pct 为净利润/售价×100。
    """
    src = max(0.0, _to_float(source_price))
    sell = max(0.0, _to_float(sell_price))
    ship = max(0.0, _to_float(shipping_cost))
    extra = max(0.0, _to_float(extra_cost))
    fee_pct = max(0.0, _to_float(platform_fee_pct))

    cost = src + ship + extra
    platform_fee = sell * fee_pct / 100.0
    net_profit = sell - cost - platform_fee
    net_margin = (net_profit / sell * 100.0) if sell > 0 else 0.0
    markup = ((sell - src) / src * 100.0) if src > 0 else 0.0

    return {
        "source_price": round(src, 2),
        "sell_price": round(sell, 2),
        "cost": round(cost, 2),
        "platform_fee": round(platform_fee, 2),
        "net_profit": round(net_profit, 2),
        "net_margin_pct": round(net_margin, 1),
        "markup_pct": round(markup, 1),
        "profitable": net_profit > 0,
    }


def _sku_min_price(product: dict[str, Any]) -> float:
    """取商品 SKU 最低价；无 SKU 则回退 original_price。"""
    sku_list = product.get("sku_list") or []
    prices = [_to_float(s.get("price")) for s in sku_list if _to_float(s.get("price")) > 0]
    if prices:
        return min(prices)
    return _to_float(product.get("original_price"))


def _total_stock(product: dict[str, Any]) -> int:
    """汇总 SKU 库存；无 SKU 信息则返回 0（未知）。"""
    sku_list = product.get("sku_list") or []
    total = 0
    has_stock = False
    for s in sku_list:
        st = _to_int(s.get("stock"))
        if st > 0:
            has_stock = True
            total += st
    return total if has_stock else 0


# 打分权重（合计 100）。净利率最重要，其次需求热度。
DEFAULT_WEIGHTS = {
    "margin": 40,    # 净利率
    "demand": 25,    # 需求热度（wants）
    "markup": 15,    # 加价空间
    "variety": 10,   # 多规格
    "stock": 10,     # 库存充足
}


def _score_margin(net_margin_pct: float) -> float:
    """净利率 → 0-1。<=0 计 0；>=50% 计满分，线性。"""
    if net_margin_pct <= 0:
        return 0.0
    return min(1.0, net_margin_pct / 50.0)


def _score_demand(wants: int, has_signal: bool) -> float:
    """需求热度 → 0-1。无 wants 数据给中性 0.5；>=500 想要计满分。"""
    if not has_signal:
        return 0.5
    if wants <= 0:
        return 0.0
    return min(1.0, wants / 500.0)


def _score_markup(markup_pct: float) -> float:
    """加价空间 → 0-1。<=0 计 0；>=100% 计满分。"""
    if markup_pct <= 0:
        return 0.0
    return min(1.0, markup_pct / 100.0)


def _score_variety(sku_count: int) -> float:
    """多规格 → 0-1。单规格 0.4（仍可卖），>=5 规格计满分。"""
    if sku_count <= 1:
        return 0.4
    return min(1.0, 0.4 + (sku_count - 1) * 0.15)


def _score_stock(total_stock: int, has_signal: bool) -> float:
    """库存充足 → 0-1。无库存信息给中性 0.5；>=200 计满分。"""
    if not has_signal:
        return 0.5
    if total_stock <= 0:
        return 0.0
    return min(1.0, total_stock / 200.0)


def _grade(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def score_product(
    product: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    shipping_cost: float = DEFAULT_SHIPPING_COST,
    platform_fee_pct: float = DEFAULT_PLATFORM_FEE_PCT,
    extra_cost: float = DEFAULT_EXTRA_COST,
    target_markup_pct: float = 0.0,
) -> dict[str, Any]:
    """对单个商品做选品打分（0-100）。

    Returns:
        {score, grade, profit, signals, reasons}
        profit 为 compute_profit 结果（取 SKU 最低价为源价、new_price 为售价）。
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: v for k, v in weights.items() if k in DEFAULT_WEIGHTS})
    w_total = sum(w.values()) or 1

    src = _sku_min_price(product)
    sell = _to_float(product.get("new_price")) or _to_float(product.get("original_price"))
    # 售价未加价（<=源价）时，可用目标加价率推算售价做"潜力"评估，
    # 避免采集后未设价时打分页全部显示亏损、失去参考价值。
    projected = False
    tmp = max(0.0, _to_float(target_markup_pct))
    if tmp > 0 and src > 0 and sell <= src:
        sell = round(src * (1 + tmp / 100.0), 2)
        projected = True
    profit = compute_profit(
        src, sell, shipping_cost=shipping_cost,
        platform_fee_pct=platform_fee_pct, extra_cost=extra_cost,
    )

    wants_raw = product.get("wants")
    has_wants = str(wants_raw or "").strip() not in ("", "None")
    wants = _to_int(wants_raw)

    sku_list = product.get("sku_list") or []
    sku_count = len(sku_list)
    total_stock = _total_stock(product)
    has_stock = total_stock > 0

    s_margin = _score_margin(profit["net_margin_pct"])
    s_demand = _score_demand(wants, has_wants)
    s_markup = _score_markup(profit["markup_pct"])
    s_variety = _score_variety(sku_count)
    s_stock = _score_stock(total_stock, has_stock)

    score = (
        s_margin * w["margin"] + s_demand * w["demand"] + s_markup * w["markup"]
        + s_variety * w["variety"] + s_stock * w["stock"]
    ) / w_total * 100.0
    score = round(score, 1)

    reasons: list[str] = []
    if not profit["profitable"]:
        reasons.append("⚠️ 按当前售价测算为亏损")
    elif profit["net_margin_pct"] >= 30:
        reasons.append(f"净利率高（{profit['net_margin_pct']}%）")
    if has_wants and wants >= 300:
        reasons.append(f"需求旺（想要 {wants}）")
    if sku_count > 1:
        reasons.append(f"多规格（{sku_count} 个）")
    if not has_wants:
        reasons.append("无需求数据（按中性计分）")
    if projected:
        reasons.append(f"售价按目标加价 {tmp:.0f}% 推算")

    return {
        "score": score,
        "grade": _grade(score),
        "projected": projected,
        "profit": profit,
        "signals": {
            "net_margin_pct": profit["net_margin_pct"],
            "wants": wants if has_wants else None,
            "markup_pct": profit["markup_pct"],
            "sku_count": sku_count,
            "total_stock": total_stock if has_stock else None,
        },
        "reasons": reasons,
    }


def rank_products(
    products: list[dict[str, Any]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    shipping_cost: float = DEFAULT_SHIPPING_COST,
    platform_fee_pct: float = DEFAULT_PLATFORM_FEE_PCT,
    extra_cost: float = DEFAULT_EXTRA_COST,
    target_markup_pct: float = 0.0,
) -> list[dict[str, Any]]:
    """对一批商品打分并按得分降序排序。返回含 product 引用与打分结果的列表。"""
    out = []
    for p in products or []:
        res = score_product(
            p, weights=weights, shipping_cost=shipping_cost,
            platform_fee_pct=platform_fee_pct, extra_cost=extra_cost,
            target_markup_pct=target_markup_pct,
        )
        out.append({"product": p, **res})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
