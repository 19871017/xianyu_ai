"""源商品复检：对比已上架商品的「采集时快照」与「当前源商品状态」，

发现以下风险并告警，防止卖出后拿不到货 / 亏本发货：
  - below_cost ：闲鱼售价 ≤ 当前源最低价（亏本，最高优先级）。
  - price_up   ：源价上涨超过阈值（毛利被压缩）。
  - sold_out   ：源商品整体售罄（所有 SKU 库存为 0，或重采为空）。
  - sku_gone   ：部分 SKU 在源端消失（买家可能下了已下架的规格）。

设计：
  - compare_source 为纯逻辑函数（不依赖浏览器），便于单测与复用。
  - RecheckEngine 用对应平台采集器重采 source_url，再调 compare_source。
    采集器依赖浏览器，故引擎不参与单测。
"""
from __future__ import annotations

from typing import Any, Callable


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


def _sku_key(sku: dict[str, Any]) -> str:
    """SKU 匹配键：优先用 source_spec（完整原始规格），回退 spec1>spec2。"""
    src = str(sku.get("source_spec") or "").strip()
    if src:
        return src
    s1 = str(sku.get("spec1") or "").strip()
    s2 = str(sku.get("spec2") or "").strip()
    return f"{s1}>{s2}" if s2 else s1


def _min_valid_price(sku_list: list[dict[str, Any]]) -> float:
    prices = [_to_float(s.get("price")) for s in sku_list or []]
    prices = [p for p in prices if p > 0]
    return min(prices) if prices else 0.0


def _total_stock(sku_list: list[dict[str, Any]]) -> int:
    return sum(max(0, _to_int(s.get("stock"))) for s in sku_list or [])


def compare_source(
    old_item: dict[str, Any],
    new_item: dict[str, Any] | None,
    listing_price: float = 0.0,
    price_up_pct: float = 10.0,
) -> dict[str, Any]:
    """对比采集快照(old)与当前源状态(new)，返回 {alerts, summary, level}。

    Args:
        old_item: 采集时的商品（含 sku_list）。
        new_item: 重采到的当前源商品（含 sku_list）；None/空表示重采失败或下架。
        listing_price: 闲鱼实际售价（用于 below_cost 判断）；0 表示不判亏本。
        price_up_pct: 源价上涨告警阈值（百分比）。

    Returns:
        {
          "alerts": [{"level","type","message"}...],
          "level": 最高告警级别（none/info/warn/critical）,
          "summary": 单行摘要,
          "old_min_price": float, "new_min_price": float,
        }
    """
    alerts: list[dict[str, str]] = []
    old_skus = old_item.get("sku_list") or []
    old_min = _min_valid_price(old_skus)

    # 重采失败 / 整体下架。
    if not new_item or not (new_item.get("sku_list") or []):
        alerts.append({
            "level": "critical",
            "type": "offline",
            "message": "源商品重采为空（可能已下架/被风控/链接失效），请人工核实。",
        })
        return _finalize(alerts, old_min, 0.0)

    new_skus = new_item.get("sku_list") or []
    new_min = _min_valid_price(new_skus)

    # 1) 整体售罄（有 SKU 但总库存为 0）。
    if new_skus and _total_stock(new_skus) <= 0:
        alerts.append({
            "level": "critical",
            "type": "sold_out",
            "message": "源商品整体售罄（所有 SKU 库存为 0），建议下架或换源。",
        })

    # 2) 部分 SKU 在源端消失。
    old_keys = {_sku_key(s) for s in old_skus if _sku_key(s)}
    new_keys = {_sku_key(s) for s in new_skus if _sku_key(s)}
    gone = [k for k in old_keys if k and k not in new_keys]
    if gone:
        sample = "、".join(gone[:3]) + ("…" if len(gone) > 3 else "")
        alerts.append({
            "level": "warn",
            "type": "sku_gone",
            "message": f"{len(gone)} 个规格在源端消失：{sample}。下单这些规格将无法补货。",
        })

    # 3) 单 SKU 售罄（存在但库存 0）。
    new_index = {_sku_key(s): s for s in new_skus if _sku_key(s)}
    sold_out_skus = [
        k for k in old_keys
        if k in new_index and _to_int(new_index[k].get("stock")) <= 0
    ]
    if sold_out_skus and "sold_out" not in {a["type"] for a in alerts}:
        sample = "、".join(sold_out_skus[:3]) + ("…" if len(sold_out_skus) > 3 else "")
        alerts.append({
            "level": "warn",
            "type": "sku_sold_out",
            "message": f"{len(sold_out_skus)} 个规格源端售罄：{sample}。",
        })

    # 4) 源价上涨超阈值。
    if old_min > 0 and new_min > 0:
        up_pct = (new_min - old_min) / old_min * 100.0
        if up_pct >= price_up_pct:
            alerts.append({
                "level": "warn",
                "type": "price_up",
                "message": (
                    f"源最低价上涨 {up_pct:.1f}%"
                    f"（¥{old_min:.2f} → ¥{new_min:.2f}），毛利被压缩。"
                ),
            })

    # 5) 亏本：闲鱼售价 ≤ 当前源最低价（最高优先级）。
    lp = _to_float(listing_price)
    if lp > 0 and new_min > 0 and lp <= new_min:
        alerts.append({
            "level": "critical",
            "type": "below_cost",
            "message": (
                f"⚠ 亏本风险：闲鱼售价 ¥{lp:.2f} ≤ 源最低价 ¥{new_min:.2f}，"
                f"卖出即亏，建议立即调价或下架。"
            ),
        })

    return _finalize(alerts, old_min, new_min)


_LEVEL_ORDER = {"none": 0, "info": 1, "warn": 2, "critical": 3}


def _finalize(alerts: list[dict[str, str]], old_min: float, new_min: float) -> dict[str, Any]:
    if not alerts:
        level = "none"
        summary = "正常：源商品价格/库存无明显异常。"
    else:
        level = max((a["level"] for a in alerts), key=lambda x: _LEVEL_ORDER.get(x, 0))
        summary = alerts[0]["message"]
    return {
        "alerts": alerts,
        "level": level,
        "summary": summary,
        "old_min_price": round(old_min, 2),
        "new_min_price": round(new_min, 2),
    }


# 平台 → 采集器类的映射（与采集页一致），延迟导入避免循环依赖。
def _collector_for(platform: str):
    from engine.alibaba_collector import AlibabaCollector
    from engine.taobao_collector import TaobaoCollector
    from engine.jd_collector import JDCollector
    from engine.pdd_collector import PddCollector
    mapping = {
        "1688": AlibabaCollector,
        "taobao": TaobaoCollector,
        "jd": JDCollector,
        "pdd": PddCollector,
    }
    return mapping.get(platform)


class RecheckEngine:
    """源商品复检引擎：按源平台重采商品并对比，产出告警。

    用法：
        eng = RecheckEngine(on_log=print)
        results = eng.recheck_products(products, price_up_pct=10)

    products 每项需含：platform/source_url/sku_list/new_price(闲鱼售价)。
    每个源平台只开一次浏览器、批量重采，降低风控与开销。
    """

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.log = on_log or (lambda m: None)

    def recheck_products(
        self,
        products: list[dict[str, Any]],
        price_up_pct: float = 10.0,
        on_item: Callable[[int, int, dict], None] | None = None,
    ) -> list[dict[str, Any]]:
        # 按平台分组，仅复检有 source_url 的商品。
        groups: dict[str, list[dict[str, Any]]] = {}
        for p in products or []:
            url = (p.get("source_url") or "").strip()
            platform = (p.get("source_platform") or p.get("platform") or "").strip()
            if not url or not _collector_for(platform):
                continue
            groups.setdefault(platform, []).append(p)

        results: list[dict[str, Any]] = []
        total = sum(len(v) for v in groups.values())
        done = 0
        if not total:
            self.log("没有可复检的商品（需有源平台与源链接）。")
            return results

        for platform, items in groups.items():
            cls = _collector_for(platform)
            self.log(f"复检 {platform}：{len(items)} 个商品…")
            collector = cls(on_progress=lambda m: self.log(f"  {m}"))
            urls = [it.get("source_url") for it in items]
            new_map: dict[str, dict] = {}
            try:
                # 优先用批量单会话接口，回退逐个。
                if hasattr(collector, "collect_by_links"):
                    fetched = collector.collect_by_links(urls) or []
                else:
                    fetched = []
                    for u in urls:
                        fetched += collector.collect_by_link(u) or []
                for it in fetched:
                    key = (it.get("source_url") or it.get("link") or "").strip()
                    if key:
                        new_map[key] = it
            except Exception as e:
                self.log(f"  ✗ {platform} 复检异常：{e}")

            for it in items:
                url = (it.get("source_url") or "").strip()
                new_item = new_map.get(url)
                listing_price = _to_float(it.get("new_price") or it.get("price"))
                cmp = compare_source(it, new_item, listing_price, price_up_pct)
                row = {
                    "db_id": it.get("db_id"),
                    "title": it.get("title") or it.get("original_title") or "",
                    "platform": platform,
                    "source_url": url,
                    "listing_price": listing_price,
                    **cmp,
                }
                results.append(row)
                done += 1
                if on_item:
                    try:
                        on_item(done, total, row)
                    except Exception:
                        pass
        return results
