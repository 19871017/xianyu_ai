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


# 中国大陆手机号（含掩码形式，如 138****1234）。
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_PHONE_MASK_RE = re.compile(r"1[3-9]\d\D{0,2}\**\D{0,2}\d{4}")
# 常见收货信息标签。
_NAME_LABELS = ("收货人", "收件人", "联系人", "姓名")
_PHONE_LABELS = ("手机", "电话", "联系电话", "手机号")
_ADDR_LABELS = ("收货地址", "收件地址", "详细地址", "地址")
# 省级行政区（用于无标签时兜底定位地址起点）。
_PROVINCE_RE = re.compile(
    r"(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|"
    r"江西|山东|河南|湖北|湖南|广东|广西|海南|四川|贵州|云南|陕西|甘肃|青海|"
    r"内蒙古|西藏|宁夏|新疆|香港|澳门|台湾)"
)


def _label_value(text: str, labels) -> str:
    """从整段文本里按「标签：值」抽取标签对应的值。

    容忍中英文冒号、标签后紧跟空白，值截到行尾或下一个明显标签前。
    """
    if not text:
        return ""
    for lab in labels:
        # 标签 + 冒号(可选) + 值，值取到换行/竖线/下一标签。
        m = re.search(lab + r"[：:\s]*([^\n\r|；;]+)", text)
        if m:
            val = m.group(1).strip()
            # 去掉值里混入的其它标签词（如"收货人张三手机..."被拆开的残留）。
            # 按长度降序切分，确保"联系电话"先于"电话"命中，避免残留"联系"。
            others = sorted(
                set(_NAME_LABELS + _PHONE_LABELS + _ADDR_LABELS) - {lab},
                key=len, reverse=True,
            )
            for other in others:
                val = val.split(other)[0].strip()
            if val:
                return val
    return ""


def parse_ship_info(text: str) -> dict[str, str]:
    """从订单详情页可见文本中解析买家收货信息。

    纯文本解析，不依赖具体 DOM 结构，抗页面改版。返回
    {name, phone, address}，抽不到的字段为空串。
    """
    text = str(text or "")
    name = _label_value(text, _NAME_LABELS)
    phone = _label_value(text, _PHONE_LABELS)
    address = _label_value(text, _ADDR_LABELS)

    # 手机号兜底：先精确 11 位，再掩码形式。
    if not _PHONE_RE.fullmatch(phone.replace(" ", "")):
        m = _PHONE_RE.search(text)
        if m:
            phone = m.group(0)
        else:
            mm = _PHONE_MASK_RE.search(text)
            if mm:
                phone = mm.group(0).strip()

    # 地址兜底：无标签时从首个省级行政区截取一段。
    if not address:
        m = _PROVINCE_RE.search(text)
        if m:
            seg = text[m.start():m.start() + 120]
            address = re.split(r"[\n\r|；;]", seg)[0].strip()

    # 姓名兜底：地址串里若带姓名/手机，剥离出来只留地址。
    if address:
        address = _PHONE_RE.sub("", address).strip(" ,，")
    return {
        "name": _txt(name, 80),
        "phone": _txt(phone, 40),
        "address": _txt(address, 300),
    }


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
        "detail_url": _txt(raw.get("detail_url") or raw.get("order_url") or "", 500),
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
          // 订单详情链接：取该行首个指向订单详情的 a[href]。
          var durl = '';
          var as = rows[r].querySelectorAll('a[href]');
          for (var a=0;a<as.length;a++){
            var h = as[a].getAttribute('href')||'';
            if (h.indexOf('order')>=0 || h.indexOf('detail')>=0){ durl = as[a].href||h; break; }
          }
          out.push({
            order_id: order_id,
            buyer_name: cell(idxBuyer),
            title: title,
            spec: cell(idxSpec),
            order_amount: amt,
            order_status: cell(idxStatus),
            image: img,
            detail_url: durl,
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

    def fetch_order_address(self, order: dict[str, Any], max_wait: int = 12) -> dict[str, str]:
        """进订单详情页补抓买家收货信息（列表页不含地址）。

        代采发货前按需调用：优先用订单行携带的 detail_url，其次退回订单页
        并按订单号定位「详情」入口。取详情页整页可见文本交给 parse_ship_info
        解析，纯文本路线抗页面改版。返回 {name, phone, address}，抓不到全空串。
        """
        empty = {"name": "", "phone": "", "address": ""}
        if not self.tab:
            self.log("浏览器未就绪，请先 open()")
            return empty

        detail_url = (order or {}).get("detail_url") or ""
        order_id = (order or {}).get("platform_order_id") or (order or {}).get("order_id") or ""

        try:
            if detail_url:
                self.tab.get(detail_url)
            else:
                # 无直链：回订单页，点该订单号所在行的「详情」入口。
                list_url = PLATFORM_URLS["goofishpro"].get("orders") or "https://goofish.pro/sale/order/all"
                self.tab.get(list_url)
                if order_id:
                    opened = self._open_detail_by_order_id(order_id, max_wait=max_wait)
                    if not opened:
                        self.log(f"未能定位订单 {order_id} 的详情入口")
                        return empty
                else:
                    self.log("订单缺少详情链接与订单号，无法补抓收货地址")
                    return empty
        except Exception as e:
            self.log(f"打开订单详情失败: {e}")
            return empty

        # 等待详情渲染：出现「收货」「地址」等关键字即停。
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                ready = self.tab.run_js(
                    "var t=document.body.innerText||'';"
                    "return t.indexOf('收货')>=0 || t.indexOf('收件')>=0 || t.indexOf('地址')>=0;"
                )
            except Exception:
                ready = False
            if ready:
                break
            time.sleep(0.5)

        try:
            text = self.tab.run_js("return document.body.innerText || '';") or ""
        except Exception as e:
            self.log(f"读取订单详情文本失败: {e}")
            return empty

        info = parse_ship_info(text)
        if info.get("address"):
            self.log(f"补抓收货地址成功：{info.get('name','')} / {info.get('address','')[:20]}…")
        else:
            self.log("未从详情页解析到收货地址，请人工核对")
        return info

    def _open_detail_by_order_id(self, order_id: str, max_wait: int = 12) -> bool:
        """在订单列表页点击指定订单号所在行的「详情/查看」入口。"""
        # 等表格就绪。
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                ready = self.tab.run_js("return !!document.querySelector('table');")
            except Exception:
                ready = False
            if ready:
                break
            time.sleep(0.5)

        js = r"""
        var oid = arguments[0];
        var rows = document.querySelectorAll('table tbody tr, table tr');
        for (var r=0;r<rows.length;r++){
          if ((rows[r].innerText||'').indexOf(oid) < 0) continue;
          var links = rows[r].querySelectorAll('a, button, span');
          for (var i=0;i<links.length;i++){
            var t = (links[i].innerText||'').trim();
            if (t.indexOf('详情')>=0 || t.indexOf('查看')>=0){ links[i].click(); return true; }
          }
          // 无显式入口：点该行首个可跳转的 a。
          var a = rows[r].querySelector('a[href]');
          if (a){ a.click(); return true; }
        }
        return false;
        """
        try:
            return bool(self.tab.run_js(js, order_id))
        except Exception as e:
            self.log(f"定位订单详情入口异常: {e}")
            return False
