"""闲鱼官方在售商品抓取 + 概览汇总。

职责：登录态下打开个人在售页（goofish.com/personal），抓取当前账号
真实在售的商品列表（标题/价格/想要/浏览/商品id），并汇总为概览指标，
落库到 monitor_snapshots（复用既有表，platform='xianyu_listing'）。

设计原则：
  - 纯逻辑（normalize_listing / summarize_listings）无浏览器依赖，便于单测。
  - 浏览器抓取（XianyuListingFetcher）只读，不点任何下架/编辑/降价按钮。
  - 抓取失败时返回空列表并记录原因，绝不误报。
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


# monitor_snapshots 里用于区分「闲鱼在售快照」的平台键。
LISTING_PLATFORM_KEY = "xianyu_listing"

PROFILE_URL = PLATFORM_URLS["xianyu"].get("profile", "https://www.goofish.com/personal")


# ─────────────────────────── 纯逻辑 ───────────────────────────

def _to_int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    m = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _txt(value: Any, max_len: int = 200) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def normalize_listing(raw: dict[str, Any]) -> dict[str, Any]:
    """把抓取到的原始在售商品字段规整为统一结构。"""
    raw = raw or {}
    return {
        "item_id": _txt(raw.get("item_id") or raw.get("itemId") or raw.get("id") or "", 64),
        "title": _txt(raw.get("title") or raw.get("item_title") or "", 200),
        "price": _to_float(raw.get("price")),
        "wants": _to_int(raw.get("wants")),
        "views": _to_int(raw.get("views")),
        "link": _txt(raw.get("link") or raw.get("href") or "", 400),
    }


def summarize_listings(listings: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总在售商品列表为概览指标。"""
    listings = [normalize_listing(x) for x in (listings or []) if isinstance(x, dict)]
    total = len(listings)
    total_wants = sum(x["wants"] for x in listings)
    total_views = sum(x["views"] for x in listings)
    prices = [x["price"] for x in listings if x["price"] > 0]
    avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0
    return {
        "active_listings": total,
        "total_wants": total_wants,
        "total_views": total_views,
        "avg_price": avg_price,
        "listings": listings,
    }


# ─────────────────────────── 浏览器抓取 ───────────────────────────

class XianyuListingFetcher:
    """闲鱼个人在售商品抓取（只读，不做任何下架/编辑/降价操作）。"""

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.log = on_log or (lambda m: None)
        self.browser = None
        self.tab = None

    def open(self, timeout: int = 600) -> bool:
        res = ensure_login("xianyu", on_log=self.log, timeout=timeout)
        if not res["ok"]:
            self.log(f"登录失败: {res.get('error')}")
            return False
        self.browser = res["browser"]
        self.tab = res["tab"]
        return True

    def close(self):
        if self.browser:
            try:
                self.browser.quit()
            except Exception:
                pass
            self.browser = None
            self.tab = None

    def fetch_listings(self, max_scroll: int = 10) -> list[dict[str, Any]]:
        """抓取个人在售商品列表，返回规整后的 dict 列表。"""
        if not self.tab:
            self.log("浏览器未就绪，请先 open()")
            return []
        try:
            self.tab.get(PROFILE_URL)
        except Exception as e:
            self.log(f"打开个人在售页失败: {e}")
            return []
        time.sleep(6)

        # 触发懒加载，把在售商品都加载出来。
        for _ in range(max_scroll):
            try:
                self.tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                break
            time.sleep(1.0)

        raw_list = self._extract_listings_js()
        listings = [normalize_listing(r) for r in raw_list if isinstance(r, dict)]
        # 去重：按 item_id。
        seen, dedup = set(), []
        for x in listings:
            key = x["item_id"] or x["link"]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            dedup.append(x)
        self.log(f"在售商品抓取：{len(dedup)} 个")
        return dedup

    def _extract_listings_js(self) -> list[dict[str, Any]]:
        """从个人在售页 DOM 抽取商品卡片。

        实测（2026-06，goofish.com/personal）：每个在售商品是一个
        ``a.feeds-item-wrap--*`` 锚点，其自身 innerText already 含
        标题/发布时间/价格/店名。价格形如 ``¥\n29\n.70``（被换行拆开），
        需去空白后再正则。想要/浏览在该页通常不展示，缺失即记 0。
        """
        js = r"""
        var out = [];
        // 个人在售卡片：class 含 feeds-item-wrap 的商品锚点。
        var anchors = document.querySelectorAll('a[class*="feeds-item-wrap"]');
        if(!anchors.length){
          // 退化兜底：带 item id 的链接。
          anchors = document.querySelectorAll('a[href*="item?id="]');
        }
        var seen = {};
        anchors.forEach(function(a){
          var href = a.href || '';
          var m = href.match(/id=(\d{8,})/);
          if(!m) return;
          var id = m[1];
          if(seen[id]) return;
          seen[id] = 1;
          var raw = (a.innerText || '').trim();
          var lines2 = raw.split('\n').map(function(s){return s.trim();}).filter(function(s){return s.length>0;});
          // 标题：首行。
          var title = lines2.length ? lines2[0] : '';
          // 价格：优先同一行 ¥29.70；否则 ¥ 单独成行，下一行为整数部，再下一行若以小数点开头则拼接。
          var price = '';
          for(var k=0;k<lines2.length;k++){
            var inline = lines2[k].match(/[¥￥]\s*([0-9]+(?:\.[0-9]+)?)/);
            if(inline){ price = inline[1]; break; }
            if(lines2[k]==='¥' || lines2[k]==='￥'){
              var ip = (lines2[k+1]||'').match(/^[0-9]+/);
              if(ip){
                price = ip[0];
                var dec = (lines2[k+2]||'').match(/^\.[0-9]+/);
                if(dec){ price += dec[0]; }
              }
              break;
            }
          }
          // 想要 / 浏览（个人在售页多数不展示，缺失记 0）。
          var wants = '';
          for(var k=0;k<lines2.length;k++){ var mw = lines2[k].match(/([0-9]+)\s*人想要/); if(mw){ wants = mw[1]; break; } }
          var views = '';
          for(var k=0;k<lines2.length;k++){ var mv = lines2[k].match(/([0-9]+)\s*次?浏览/); if(mv){ views = mv[1]; break; } }
          out.push({item_id:id, title:title, price:price, wants:wants, views:views, link:href});
        });
        return out;
        """
        try:
            data = self.tab.run_js(js)
            return data if isinstance(data, list) else []
        except Exception as e:
            self.log(f"在售商品抽取异常: {e}")
            return []

def fetch_and_store(on_log: Callable[[str], None] | None = None,
                    timeout: int = 600) -> dict[str, Any]:
    """完整流程：登录 → 抓在售 → 汇总 → 落库。返回汇总结果。"""
    log = on_log or (lambda m: None)
    fetcher = XianyuListingFetcher(on_log=log)
    out = {"ok": False, "active_listings": 0, "total_wants": 0,
           "total_views": 0, "avg_price": 0.0, "listings": [], "error": ""}
    try:
        if not fetcher.open(timeout=timeout):
            out["error"] = "闲鱼登录失败"
            return out
        listings = fetcher.fetch_listings()
        summary = summarize_listings(listings)
        out.update(summary)
        out["ok"] = True
        _store_snapshot(summary, log)
    except Exception as e:
        out["error"] = str(e)
        log(f"抓取在售商品异常: {e}")
    finally:
        fetcher.close()
    return out


def _store_snapshot(summary: dict[str, Any], log: Callable[[str], None]) -> None:
    """把在售汇总落库到 monitor_snapshots（platform=xianyu_listing）。"""
    try:
        from database.db_manager import db
        db.save_monitor_snapshot({
            "platform": LISTING_PLATFORM_KEY,
            "is_logged_in": True,
            "active_listings": summary.get("active_listings", 0),
            "total_wants": summary.get("total_wants", 0),
            "total_views": summary.get("total_views", 0),
            "raw_data": {"avg_price": summary.get("avg_price", 0.0),
                         "listings": summary.get("listings", [])},
        })
    except Exception as e:
        log(f"在售快照落库失败: {e}")


def get_latest_listing_summary() -> dict[str, Any] | None:
    """读取最近一次闲鱼在售快照（供概览展示）。"""
    try:
        from database.db_manager import db
        snap = db.get_latest_monitor_snapshot(LISTING_PLATFORM_KEY)
    except Exception:
        snap = None
    if not snap:
        return None
    raw = snap.get("raw_data")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    raw = raw or {}
    return {
        "active_listings": snap.get("active_listings", 0),
        "total_wants": snap.get("total_wants", 0),
        "total_views": snap.get("total_views", 0),
        "avg_price": raw.get("avg_price", 0.0),
        "snapshot_time": snap.get("snapshot_time", ""),
    }
