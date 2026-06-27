"""1688 详情页 SKU 解析（纯函数，无浏览器依赖，便于单元测试）。

1688 详情页（ICE 框架）把商品数据内嵌在页面的大 JSON blob 中，
``skuModel`` 描述了多规格多价格信息，远比抓 DOM class 稳定。

真实结构（2024+ 详情页）形如：
    "skuModel": {
        "skuProps": [
            {"fid":3216, "prop":"颜色", "value":[{"name":"黑色套装", "imageUrl":"..."}]},
            {"fid":450,  "prop":"尺码", "value":[{"name":"均码"}]}
        ],
        "skuInfoMap": {
            "黑色套装>均码": {"specId":"...", "specAttrs":"黑色套装>均码",
                              "price":"4.50", "discountPrice":"4.50",
                              "canBookCount":190, "skuId":5196460270576}
        }
    }

注意：skuInfoMap 的 key 是“规格名直接拼接”（分隔符在 HTML 里是 ``&gt;``），
而不是 vid 组合。本模块同时兼容旧的 vid 结构（value 带 vid）。

数据来源可能缺失或结构有差异，所有解析都做容错，解析不到时返回空列表，
由上层决定是否回退到单价格商品。
"""

from __future__ import annotations

import json
import re
from typing import Any


# 详情页里常见的内嵌 JSON 变量名（旧版/兜底）
_INIT_VAR_PATTERNS = [
    r"window\.__INIT_DATA__\s*=\s*",
    r"window\.__GLOBAL_DATA__?\s*=\s*",
    r"window\.detailData\s*=\s*",
    r"window\.__AT_INIT_DATA__\s*=\s*",
    r"window\.runParams\s*=\s*",
]

# skuInfoMap key / specAttrs 里的规格分隔符（含 HTML 实体形式）
_SPEC_SEP = re.compile(r"(?:&gt;|&#62;|&#x3e;|＞|>)", re.I)


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
    # 去掉 _800x800 之类尺寸段，但保留扩展名
    url = re.sub(r"_\d+x\d+(xz)?(?=\.(jpg|jpeg|png|webp))", "", url, flags=re.I)
    # 去掉末尾 .jpg_.webp 这种二次后缀
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


def extract_init_json(html: str) -> dict[str, Any] | None:
    """从详情页 HTML 文本中抽取内嵌的初始化 JSON 对象（旧版 window 变量）。"""
    if not html or not isinstance(html, str):
        return None
    for pat in _INIT_VAR_PATTERNS:
        for m in re.finditer(pat, html):
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


def _find_sku_model(data: Any, depth: int = 0) -> dict[str, Any] | None:
    """在任意嵌套结构里定位 skuModel（含 skuInfoMap / skuProps）。"""
    if depth > 10 or data is None:
        return None
    if isinstance(data, dict):
        lower = {k.lower(): k for k in data.keys()}
        for key in ("skumodel", "sku_model"):
            if key in lower:
                model = data[lower[key]]
                if isinstance(model, dict) and (
                    "skuInfoMap" in model or "skuProps" in model
                    or "skuPriceScale" in model
                ):
                    return model
        if "skuInfoMap" in data or ("skuProps" in data and "skuInfoMap" in data):
            return data
        for value in data.values():
            found = _find_sku_model(value, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_sku_model(value, depth + 1)
            if found:
                return found
    return None


def _extract_skumodel_blob(html: str) -> dict[str, Any] | None:
    """直接从 HTML 文本里定位 ``"skuModel":{...}`` 并解析（ICE 框架页面）。"""
    if not html or not isinstance(html, str):
        return None
    for m in re.finditer(r'"skuModel"\s*:\s*', html):
        brace_start = html.find("{", m.end())
        if brace_start == -1:
            continue
        blob = _match_brace(html, brace_start)
        if not blob:
            continue
        try:
            model = json.loads(blob)
        except Exception:
            continue
        if isinstance(model, dict) and ("skuInfoMap" in model or "skuProps" in model):
            return model
    return None


def _build_value_index(sku_props: list) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """把 skuProps 摊平成两份索引：按 vid、按规格名。

    返回 (by_vid, by_name)，每个值为 {"prop":规格名, "name":规格值, "image":图}。
    """
    by_vid: dict[str, dict[str, str]] = {}
    by_name: dict[str, dict[str, str]] = {}
    for prop in sku_props or []:
        if not isinstance(prop, dict):
            continue
        prop_name = prop.get("prop") or prop.get("propName") or prop.get("name") or ""
        for val in prop.get("value") or prop.get("values") or []:
            if not isinstance(val, dict):
                continue
            vid = str(val.get("vid") or val.get("valueId") or val.get("id") or "")
            name = str(val.get("name") or val.get("value") or "").strip()
            image = _normalize_image_url(val.get("imageUrl") or val.get("image") or "")
            meta = {"prop": str(prop_name), "name": name, "image": image}
            if vid:
                by_vid[vid] = meta
            if name:
                by_name[name] = meta
    return by_vid, by_name


def parse_sku_model(sku_model: dict[str, Any]) -> list[dict[str, Any]]:
    """把 1688 skuModel 解析成标准 sku_list。

    返回的每个 sku 含：spec1/spec2/price/stock/sku_image_url/merchant_sku/sku_attrs。
    同时兼容两种结构：
      1. 真实 ICE 页面：skuInfoMap key 为规格名拼接，value 自带 specAttrs。
      2. 旧版/合成：skuProps.value 带 vid，skuInfoMap key 为 vid 组合。
    """
    if not isinstance(sku_model, dict):
        return []

    by_vid, by_name = _build_value_index(
        sku_model.get("skuProps") or sku_model.get("skuProa") or []
    )
    info_map = (
        sku_model.get("skuInfoMap")
        or sku_model.get("skuMap")
        or {}
    )
    if not isinstance(info_map, dict) or not info_map:
        return []

    sku_list: list[dict[str, Any]] = []
    for combo_key, info in info_map.items():
        if not isinstance(info, dict):
            continue

        # 优先用 specAttrs（更干净），否则用 map 的 key
        key_source = info.get("specAttrs") or combo_key
        tokens = [t.strip() for t in _SPEC_SEP.split(str(key_source)) if t.strip()]

        specs: list[str] = []
        spec_image = ""
        sku_attrs: dict[str, str] = {}
        for tok in tokens:
            meta = by_vid.get(tok) or by_name.get(tok)
            if meta:
                name = meta["name"] or tok
                specs.append(name)
                if meta["prop"] and name:
                    sku_attrs[meta["prop"]] = name
                if not spec_image and meta["image"]:
                    spec_image = meta["image"]
            else:
                specs.append(tok)

        price = _to_float(
            info.get("price")
            or info.get("discountPrice")
            or info.get("priceText")
            or info.get("retailPrice")
        )
        stock = _to_int(
            info.get("canBookCount")
            or info.get("stock")
            or info.get("quantity")
            or info.get("saleCount"),
            default=0,
        )
        merchant_sku = str(
            info.get("skuId") or info.get("specId") or combo_key or ""
        )

        spec1 = specs[0] if specs else "默认"
        spec2 = specs[1] if len(specs) > 1 else ""

        sku_list.append({
            "spec1": spec1,
            "spec2": spec2,
            "price": price,
            "stock": stock,
            "sku_image_url": spec_image,
            "merchant_sku": merchant_sku,
            "sku_attrs": sku_attrs,
        })

    return sku_list


def parse_sku_from_html(html: str) -> list[dict[str, Any]]:
    """便捷入口：HTML → sku_list（解析不到返回空列表）。"""
    # 路线 1：旧版 window 变量
    data = extract_init_json(html)
    if data:
        model = _find_sku_model(data)
        if model:
            skus = parse_sku_model(model)
            if skus:
                return skus
    # 路线 2：直接定位 skuModel blob（ICE 框架页面）
    model = _extract_skumodel_blob(html)
    if model:
        return parse_sku_model(model)
    return []
