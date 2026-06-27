"""淘宝/天猫详情页 SKU 解析（纯函数，无浏览器依赖，便于单元测试）。

淘宝/天猫详情页（ICE/React 框架）把商品数据内嵌在页面的多个 JSON blob 中，
与 1688 的 ``skuModel`` 结构不同，淘宝拆成 ``skuBase`` + ``skuCore`` 两块：

    "skuBase": {
        "props": [
            {"pid":"1627207", "name":"颜色分类",
             "valueMap":{"43132530481":{"vid":"43132530481","name":"红色","image":"..."}}},
            {"pid":"5919063", "name":"套餐类型",
             "valueMap":{"6536025":{"vid":"6536025","name":"官方标配"}}}
        ],
        "skus": [
            {"propPath":"1627207:43132530481;5919063:6536025", "skuId":"6170719666990"}
        ]
    },
    "skuCore": {
        "sku2info": {
            "0": {"quantity":200, "price":{"priceMoney":"10500","priceText":"105"}},
            "6170719666990": {"quantity":9, "price":{"priceMoney":"14900","priceText":"149"}}
        }
    }

要点：
- ``propPath`` 是 ``pid:vid;pid:vid`` 拼接，需用 props 的 valueMap 反查规格名/图。
- ``sku2info`` 的 key 是 skuId；``priceMoney`` 是「分」(10500=¥105)，``priceText`` 是「元」。
- key ``"0"`` 是默认/起始价聚合项，不是真实组合，按 skuId 匹配时自动跳过。

数据来源可能缺失或结构有差异，所有解析都做容错，解析不到时返回空列表，
由上层决定是否回退到单价格商品。
"""
from __future__ import annotations

import json
import re
from typing import Any


def _match_brace(text: str, start: int) -> str | None:
    """从 ``start`` 处的 '{' 开始，做括号配平，返回完整 JSON 子串。"""
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _grab_blob(html: str, key: str) -> dict[str, Any] | None:
    """定位 ``"key":{...}`` 并解析为 dict（取第一个可解析命中）。"""
    if not html or not isinstance(html, str):
        return None
    for m in re.finditer(re.escape(f'"{key}"') + r"\s*:\s*", html):
        brace_start = html.find("{", m.end())
        if brace_start == -1:
            continue
        blob = _match_brace(html, brace_start)
        if not blob:
            continue
        try:
            data = json.loads(blob)
        except Exception:
            continue
        if isinstance(data, dict) and data:
            return data
    return None


def _normalize_image_url(url: str) -> str:
    """统一图片 URL：补协议、去尺寸后缀取大图。"""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    url = re.sub(r"_\d+x\d+(xz)?(?=\.(jpg|jpeg|png|webp))", "", url, flags=re.I)
    url = re.sub(r"\.(jpg|jpeg|png)_\.webp$", r".\1", url, flags=re.I)
    return url


def _to_float(value: Any) -> float:
    """从任意价格表示里提取数值，区间价取最低。"""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", "").replace("，", "")
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return 0.0
    try:
        return round(min(float(n) for n in nums), 2)
    except Exception:
        return 0.0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _price_from_info(info: dict[str, Any]) -> float:
    """从 sku2info 单项里取价格：优先 priceMoney(分)，回退 priceText(元)。"""
    if not isinstance(info, dict):
        return 0.0
    price = info.get("price")
    if isinstance(price, dict):
        money = price.get("priceMoney")
        if money not in (None, ""):
            try:
                return round(int(float(str(money).replace(",", ""))) / 100, 2)
            except Exception:
                pass
        text = price.get("priceText") or price.get("priceDesc")
        if text:
            return _to_float(text)
    # 极少数页面 price 是字符串
    if isinstance(price, (str, int, float)):
        return _to_float(price)
    return 0.0


def _build_prop_index(props: list) -> dict[str, dict[str, Any]]:
    """把 skuBase.props 摊平成 ``pid -> {name, values:{vid:{name,image}}}``。"""
    index: dict[str, dict[str, Any]] = {}
    for prop in props or []:
        if not isinstance(prop, dict):
            continue
        pid = str(prop.get("pid") or "")
        if not pid:
            continue
        prop_name = prop.get("name") or prop.get("prop") or ""
        values: dict[str, dict[str, str]] = {}
        value_map = prop.get("valueMap")
        if isinstance(value_map, dict):
            iterable = value_map.values()
        else:
            iterable = prop.get("values") or []
        for val in iterable:
            if not isinstance(val, dict):
                continue
            vid = str(val.get("vid") or val.get("valueId") or val.get("id") or "")
            if not vid:
                continue
            values[vid] = {
                "name": str(val.get("name") or val.get("value") or "").strip(),
                "image": _normalize_image_url(val.get("image") or val.get("imageUrl") or ""),
            }
        index[pid] = {"name": str(prop_name), "values": values}
    return index


def parse_sku(sku_base: dict[str, Any], sku_core: dict[str, Any]) -> list[dict[str, Any]]:
    """把淘宝 skuBase + skuCore 解析成标准 sku_list。

    返回的每个 sku 含：spec1/spec2/price/stock/sku_image_url/merchant_sku/sku_attrs。
    """
    if not isinstance(sku_base, dict):
        return []
    skus = sku_base.get("skus")
    if not isinstance(skus, list) or not skus:
        return []

    prop_index = _build_prop_index(sku_base.get("props") or [])
    sku2info = {}
    if isinstance(sku_core, dict):
        info = sku_core.get("sku2info")
        if isinstance(info, dict):
            sku2info = info

    sku_list: list[dict[str, Any]] = []
    for sku in skus:
        if not isinstance(sku, dict):
            continue
        sku_id = str(sku.get("skuId") or sku.get("skuid") or "")
        prop_path = str(sku.get("propPath") or sku.get("pvs") or "")
        if not prop_path:
            continue

        specs: list[str] = []
        spec_image = ""
        sku_attrs: dict[str, str] = {}
        for pair in prop_path.split(";"):
            pair = pair.strip()
            if ":" not in pair:
                continue
            pid, vid = pair.split(":", 1)
            pinfo = prop_index.get(pid.strip())
            if not pinfo:
                continue
            vinfo = pinfo["values"].get(vid.strip())
            if not vinfo:
                continue
            name = vinfo["name"]
            if name:
                specs.append(name)
                if pinfo["name"]:
                    sku_attrs[pinfo["name"]] = name
            if not spec_image and vinfo["image"]:
                spec_image = vinfo["image"]

        info = sku2info.get(sku_id, {}) if sku_id else {}
        price = _price_from_info(info)
        stock = _to_int(info.get("quantity") or info.get("stock"), default=0)

        spec1 = specs[0] if specs else "默认"
        spec2 = specs[1] if len(specs) > 1 else ""

        sku_list.append({
            "spec1": spec1,
            "spec2": spec2,
            "price": price,
            "stock": stock,
            "sku_image_url": spec_image,
            "merchant_sku": sku_id,
            "sku_attrs": sku_attrs,
        })

    return sku_list


def parse_sku_from_html(html: str) -> list[dict[str, Any]]:
    """便捷入口：HTML → sku_list（解析不到返回空列表）。"""
    if not html or not isinstance(html, str):
        return []
    sku_base = _grab_blob(html, "skuBase")
    if not sku_base:
        return []
    sku_core = _grab_blob(html, "skuCore") or {}
    return parse_sku(sku_base, sku_core)


def extract_head_images(html: str, limit: int = 30) -> list[str]:
    """从 componentsVO.headImageVO.images 提取商品主图列表。"""
    if not html or not isinstance(html, str):
        return []
    comp = _grab_blob(html, "componentsVO")
    images: list[str] = []
    seen: set[str] = set()
    if isinstance(comp, dict):
        head = comp.get("headImageVO")
        if isinstance(head, dict):
            for url in head.get("images") or []:
                norm = _normalize_image_url(url)
                if norm and norm not in seen:
                    seen.add(norm)
                    images.append(norm)
    return images[:limit]
