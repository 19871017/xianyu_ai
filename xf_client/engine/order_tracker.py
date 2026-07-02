"""闲鱼卖出订单跟踪 + 回溯上游源商品。

职责分两层：
1) 纯逻辑（可单测，无浏览器依赖）：
   - normalize_order(raw)              规整原始订单字段。
   - match_order_to_product(order, products)
                                       订单 → 本地商品（优先闲鱼商品 id，其次标题）。
   - match_sku_for_order(order, product)
                                       买家所选规格 → 本地 SKU → 源 skuId / 源链接。
   - build_reorder_plan(order, product)
                                       生成「回上游一键代采」所需的下单计划（不下单）。
2) 浏览器抓取（XianyuOrderTracker）：
   - 走 utils.login_manager 统一登录，读 goofish.com/sold 已售订单列表。
   - 仅做只读抓取，不做任何下单/支付动作。

设计原则：默认半自动——代采只生成计划并打开上游确认页，最终支付由人工确认。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


# ─────────────────────────── 纯逻辑 ───────────────────────────

def _txt(value: Any, max_len: int = 200) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s[:max_len]


def _digits(value: Any) -> str:
    m = re.findall(r"\d+", str(value or ""))
    return "".join(m)


def _amount(value: Any) -> str:
    """从任意金额文本中抽取数值，返回如 '15.60'，抽不到返回空串。"""
    m = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return m.group(0) if m else ""


def normalize_order(raw: dict[str, Any]) -> dict[str, Any]:
    """把抓取到的原始订单字段规整为统一结构。"""
    raw = raw or {}
    item_id = _txt(
        raw.get("xianyu_item_id")
        or raw.get("item_id")
        or raw.get("itemId")
        or raw.get("goods_id")
        or ""
    , 64)
    order = {
        "platform": _txt(raw.get("platform") or "xianyu", 32),
        "platform_order_id": _txt(
            raw.get("platform_order_id") or raw.get("order_id") or raw.get("bizOrderId") or "", 64
        ),
        "xianyu_item_id": item_id,
        "title": _txt(raw.get("title") or raw.get("item_title") or raw.get("goods_name") or "", 200),
        "buyer_name": _txt(raw.get("buyer_name") or raw.get("buyer") or raw.get("buyerNick") or "", 80),
        "buyer_spec": _txt(raw.get("buyer_spec") or raw.get("spec") or raw.get("sku") or raw.get("sku_text") or "", 200),
        "quantity": _to_int(raw.get("quantity") or raw.get("qty") or raw.get("buyAmount") or 1, 1),
        "order_amount": _amount(raw.get("order_amount") or raw.get("amount") or raw.get("payAmount") or raw.get("price") or ""),
        "buyer_address": _txt(raw.get("buyer_address") or raw.get("address") or "", 300),
        "buyer_phone": _txt(raw.get("buyer_phone") or raw.get("phone") or "", 40),
        "order_status": _txt(raw.get("order_status") or raw.get("status") or "pending", 40),
        "raw": raw.get("raw") or {},
    }
    return order


def _to_int(value: Any, default: int = 1) -> int:
    try:
        n = int(float(str(value).replace(",", "")))
        return n if n > 0 else default
    except Exception:
        return default


def _norm_spec_text(text: str) -> str:
    """规格文本归一化：去标点/空白/分隔符，便于模糊比较。"""
    if not text:
        return ""
    s = str(text).lower()
    s = re.sub(r"[:：;；,，、\s/|>＞-]+", "", s)
    return s


def match_order_to_product(
    order: dict[str, Any], products: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """订单 → 本地商品。优先用闲鱼商品 id 精确匹配，其次标题包含匹配。"""
    if not products:
        return None
    xy_id = _digits(order.get("xianyu_item_id"))
    if xy_id:
        for p in products:
            if _digits(p.get("xianyu_item_id")) == xy_id and xy_id:
                return p
    title = _norm_spec_text(order.get("title") or "")
    if title:
        best = None
        best_len = 0
        for p in products:
            pt = _norm_spec_text(p.get("title") or p.get("original_title") or "")
            if not pt:
                continue
            # 双向包含，取较长命中。
            if pt in title or title in pt:
                hit = min(len(pt), len(title))
                if hit > best_len:
                    best_len = hit
                    best = p
        if best is not None:
            return best
    return None


def match_sku_for_order(
    order: dict[str, Any], product: dict[str, Any]
) -> dict[str, Any]:
    """买家所选规格 → 本地 SKU，回出源 skuId / 源链接，用于上游下单。

    返回 {ok, sku, source_sku_id, source_url, source_platform, score, note}
    score: 1.0 精确, 0.x 模糊, 0 仅回退首个。
    """
    out = {
        "ok": False,
        "sku": None,
        "source_sku_id": "",
        "source_url": product.get("source_url", "") if product else "",
        "source_platform": product.get("source_platform", "") if product else "",
        "score": 0.0,
        "note": "",
    }
    if not product:
        out["note"] = "无对应本地商品"
        return out

    sku_list = product.get("sku_list") or []
    if not sku_list:
        out["note"] = "本地商品无 SKU 数据"
        return out

    buyer = _norm_spec_text(order.get("buyer_spec") or "")

    # 单 SKU：直接命中。
    if len(sku_list) == 1:
        out.update(_sku_result(sku_list[0], product, score=1.0, note="单规格直配"))
        return out

    # 多 SKU：买家规格文本与每个 SKU 的 spec1/spec2/source_spec 做模糊比较。
    if not buyer:
        out.update(_sku_result(sku_list[0], product, score=0.0,
                               note="订单未带规格，回退首个 SKU（需人工确认）"))
        return out

    best = None
    best_score = -1.0
    for sku in sku_list:
        cand = " ".join([
            str(sku.get("spec1") or ""),
            str(sku.get("spec2") or ""),
            str(sku.get("source_spec") or ""),
        ])
        cand_n = _norm_spec_text(cand)
        if not cand_n:
            continue
        score = _spec_similarity(buyer, cand_n)
        if score > best_score:
            best_score = score
            best = sku

    if best is not None and best_score >= 0.99:
        out.update(_sku_result(best, product, score=1.0, note="规格精确匹配"))
    elif best is not None and best_score > 0:
        out.update(_sku_result(best, product, score=round(best_score, 3),
                               note="规格模糊匹配（建议人工确认）"))
    else:
        out.update(_sku_result(sku_list[0], product, score=0.0,
                               note="规格未命中，回退首个 SKU（需人工确认）"))
    return out


def _spec_similarity(buyer_norm: str, cand_norm: str) -> float:
    """规格相似度：包含关系=1.0，否则按字符重叠占比估算。"""
    if not buyer_norm or not cand_norm:
        return 0.0
    if buyer_norm == cand_norm:
        return 1.0
    if cand_norm in buyer_norm or buyer_norm in cand_norm:
        return 1.0
    common = set(buyer_norm) & set(cand_norm)
    if not common:
        return 0.0
    return len(common) / max(len(set(buyer_norm)), len(set(cand_norm)))


def _sku_result(sku: dict[str, Any], product: dict[str, Any], score: float, note: str) -> dict[str, Any]:
    return {
        "ok": True,
        "sku": sku,
        "source_sku_id": _txt(sku.get("source_sku_id") or sku.get("merchant_sku") or "", 64),
        "source_url": _txt(product.get("source_url") or "", 500),
        "source_platform": _txt(product.get("source_platform") or "", 32),
        "score": score,
        "note": note,
    }


def build_reorder_plan(order: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    """生成回上游一键代采的下单计划（仅计划，不下单）。"""
    match = match_sku_for_order(order, product)
    plan = {
        "ok": match["ok"] and bool(match["source_url"]),
        "source_platform": match["source_platform"],
        "source_url": match["source_url"],
        "source_sku_id": match["source_sku_id"],
        "spec_score": match["score"],
        "spec_note": match["note"],
        "quantity": order.get("quantity", 1),
        "ship_to": {
            "name": order.get("buyer_name", ""),
            "phone": order.get("buyer_phone", ""),
            "address": order.get("buyer_address", ""),
        },
        "sku": match["sku"],
        "note": "",
    }
    if not match["source_url"]:
        plan["ok"] = False
        plan["note"] = "缺少源商品链接，无法回上游下单"
    elif match["score"] < 0.99:
        plan["note"] = "规格非精确匹配，下单前请人工核对规格"
    return plan


# ─────────────────────── 浏览器只读抓取 ───────────────────────

class XianyuOrderTracker:
    """闲鱼已售订单抓取（只读，不下单）。

    走 utils.login_manager 统一登录态，读取 goofish.com/sold 列表。
    页面 DOM 随闲鱼改版可能变化，抓取失败时返回空列表并记录原因。
    """

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

    def fetch_sold_orders(self, max_scroll: int = 6) -> list[dict[str, Any]]:
        """抓取已售订单列表，返回规整后的订单 dict 列表。"""
        if not self.tab:
            self.log("浏览器未就绪，请先 open()")
            return []
        url = PLATFORM_URLS["xianyu"].get("orders") or "https://www.goofish.com/sold"
        self.tab.get(url)
        time.sleep(6)

        # 触发懒加载。
        for _ in range(max_scroll):
            try:
                self.tab.run_js("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                break
            time.sleep(1.2)

        raw_list = self._extract_orders_js()
        orders = [normalize_order(r) for r in raw_list if isinstance(r, dict)]
        self.log(f"已售订单抓取：{len(orders)} 条")
        return orders

    def _extract_orders_js(self) -> list[dict[str, Any]]:
        """从已售页 DOM 抽取订单卡片。DOM class 为 hash，按结构+文本启发式抽取。"""
        js = r"""
        var out = [];
        // 已售订单卡片：含商品链接(item?id=)的卡片容器。
        var anchors = document.querySelectorAll('a[href*="item?id="], a[href*="item/"]');
        var seen = {};
        anchors.forEach(function(a){
          var href = a.href || '';
          var m = href.match(/item[/?](?:id=)?(\d{8,})/);
          if(!m) return;
          var id = m[1];
          // 向上找卡片容器（最多 6 层）。
          var card = a;
          for(var i=0;i<6 && card && card.parentElement;i++){ card = card.parentElement; }
          var text = (card ? (card.innerText||'') : '').trim();
          if(seen[id]) return;
          seen[id] = 1;
          // 标题：取 a 自身文本或图片 alt。
          var title = (a.innerText||'').trim();
          if(!title){ var img=a.querySelector('img'); if(img) title=(img.alt||'').trim(); }
          // 金额：卡片内 ¥xx。
          var amt=''; var mm=text.match(/¥\s*([0-9]+(?:\.[0-9]+)?)/); if(mm) amt=mm[1];
          out.push({xianyu_item_id:id, title:title, order_amount:amt, raw:{text:text.slice(0,300)}});
        });
        return out;
        """
        try:
            data = self.tab.run_js(js)
            return data if isinstance(data, list) else []
        except Exception as e:
            self.log(f"订单抽取异常: {e}")
            return []


# ─────────────────── 闲管家(goofish.pro)卖家订单抓取 ───────────────────

class GofishproOrderTracker:
    """闲管家(goofish.pro) 卖家订单抓取（只读，不下单/不支付）。

    闲鱼官方网页版已下线卖家订单页(goofish.com/sold 返回 404)，改用闲管家
    ``/sale/order/all`` 抓单。该页为标准 <table>，免费账号即可查看，字段含
    买家昵称/商品标题/规格/金额/状态/各时间，规格用于回溯源头 SKU。

    列表页不含收货地址（需进订单详情才有），代采填地址时再按需补抓。
    """

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.log = on_log or (lambda m: None)
        self.browser = None
        self.tab = None

    def open(self, timeout: int = 600) -> bool:
        res = ensure_login("goofishpro", on_log=self.log, timeout=timeout)
        if not res["ok"]:
            self.log(f"闲管家登录失败: {res.get('error')}")
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

    def fetch_sold_orders(self, max_wait: int = 15) -> list[dict[str, Any]]:
        """抓取闲管家卖家订单列表，返回规整后的订单 dict 列表。"""
        if not self.tab:
            self.log("浏览器未就绪，请先 open()")
            return []
        url = PLATFORM_URLS["goofishpro"].get("orders") or "https://goofish.pro/sale/order/all"
        try:
            self.tab.get(url)
        except Exception as e:
            self.log(f"打开闲管家订单页失败: {e}")
            return []

        # 等表格渲染（Ant/El 表格异步加载）：出现表头或「暂无数据」即停。
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                ready = self.tab.run_js(
                    "return !!document.querySelector('table') && "
                    "(document.body.innerText.indexOf('订单编号')>=0 || "
                    "document.body.innerText.indexOf('暂无数据')>=0);"
                )
            except Exception:
                ready = False
            if ready:
                break
            time.sleep(0.6)

        raw_list = self._extract_orders_js()
        orders = [normalize_order(r) for r in raw_list if isinstance(r, dict)]
        self.log(f"闲管家订单抓取：{len(orders)} 条")
        return orders

    def _extract_orders_js(self) -> list[dict[str, Any]]:
        """从闲管家订单表格按表头列名抽取每行订单。

        表头随版本可能增删列，故按「列名文本 → 列索引」动态映射，不写死顺序。
        """
        js = r"""
        function norm(s){ return (s||'').replace(/\s+/g,'').trim(); }
        // 找到含「订单编号」表头的那张表。
        var tables = document.querySelectorAll('table');
        var target = null;
        for (var t=0; t<tables.length; t++){
          var htxt = (tables[t].innerText||'');
          if (htxt.indexOf('订单编号')>=0 || htxt.indexOf('买家昵称')>=0){ target = tables[t]; break; }
        }
        if(!target) return [];
        // 表头列名 → 索引。
        var headCells = [];
        var thead = target.querySelector('thead');
        if (thead){
          var ths = thead.querySelectorAll('th, td');
          for (var i=0;i<ths.length;i++){ headCells.push(norm(ths[i].innerText)); }
        }
        function colIdx(names){
          for (var i=0;i<headCells.length;i++){
            for (var j=0;j<names.length;j++){ if (headCells[i].indexOf(names[j])>=0) return i; }
          }
          return -1;
        }
        var idxOrder = colIdx(['订单编号','订单号']);
        var idxBuyer = colIdx(['买家昵称','买家']);
        var idxTitle = colIdx(['商品标题','标题']);
        var idxSpec  = colIdx(['规格']);
        var idxRecv  = colIdx(['实收金额','实收']);
        var idxTotal = colIdx(['商品总价','总价','金额']);
        var idxStatus= colIdx(['订单状态','状态']);
        var out = [];
        var body = target.querySelector('tbody') || target;
        var rows = body.querySelectorAll('tr');
        for (var r=0;r<rows.length;r++){
          var tds = rows[r].querySelectorAll('td');
          if (!tds.length) continue;
          function cell(ix){ return (ix>=0 && ix<tds.length) ? (tds[ix].innerText||'').trim() : ''; }
          var order_id = cell(idxOrder);
          var title = cell(idxTitle);
          // 跳过空行/无标题无订单号的行。
          if (!order_id && !title) continue;
          var amt = cell(idxRecv) || cell(idxTotal);
          // 商品图片：优先取该行第一张 img 的 src。
          var img = ''; var im = rows[r].querySelector('img'); if(im) img = im.src||'';
          out.push({
            order_id: order_id,
            buyer_name: cell(idxBuyer),
            title: title,
            spec: cell(idxSpec),
            order_amount: amt,
            order_status: cell(idxStatus),
            image: img,
            raw: {text: (rows[r].innerText||'').slice(0,300)}
          });
        }
        return out;
        """
        try:
            data = self.tab.run_js(js)
            return data if isinstance(data, list) else []
        except Exception as e:
            self.log(f"闲管家订单抽取异常: {e}")
            return []
