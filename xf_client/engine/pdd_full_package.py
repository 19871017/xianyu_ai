"""拼多多完整商品包提取辅助。

本模块只做“浏览器页面已可见/已加载数据”的解析，不做验证码绕过、签名逆向或请求伪造。
"""

from __future__ import annotations

import json
import re
from typing import Any

from engine.product_package import ensure_full_product_package


def _clean_text(value: Any, max_len: int = 1000) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:max_len]


def _safe_price(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        val = float(value)
        return round(val / 100, 2) if val > 500 else round(val, 2)
    nums = re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not nums:
        return 0.0
    val = float(nums[0])
    return round(val / 100, 2) if val > 500 else round(val, 2)


def _walk(data: Any, depth: int = 0):
    if depth > 8:
        return
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _walk(value, depth + 1)
    elif isinstance(data, list):
        for item in data:
            yield from _walk(item, depth + 1)


def _normalize_image(url: str) -> str:
    if not url:
        return ""
    url = str(url).strip().replace("&amp;", "&")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return ""
    return url.split("#")[0]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        value = _normalize_image(value)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def extract_attrs_from_raw(raw: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for obj in _walk(raw):
        for field in ("goods_property", "goodsProperty", "attrs", "attributes", "properties", "props"):
            value = obj.get(field)
            if isinstance(value, list):
                for row in value:
                    if not isinstance(row, dict):
                        continue
                    key = row.get("key") or row.get("name") or row.get("k") or row.get("pname") or row.get("attr_name")
                    val = row.get("value") or row.get("v") or row.get("values") or row.get("pvalue") or row.get("attr_value")
                    if key and val:
                        attrs[_clean_text(key, 40)] = _clean_text(val, 120)
            elif isinstance(value, dict):
                for key, val in value.items():
                    if key and val and len(str(key)) <= 40:
                        attrs[_clean_text(key, 40)] = _clean_text(val, 120)

        # 常见中文属性散落字段。
        for key, val in obj.items():
            if not isinstance(key, str) or len(key) > 30:
                continue
            if key in ("品牌", "材质", "风格", "图案元素", "产地", "发货地", "工艺", "货号", "型号"):
                attrs[key] = _clean_text(val, 120)
    return attrs


def extract_images_from_raw(raw: Any) -> tuple[list[str], list[str]]:
    main_urls: list[str] = []
    detail_urls: list[str] = []
    main_keys = {
        "image_url", "imageUrl", "thumb_url", "thumbUrl", "hd_thumb_url", "hdThumbUrl",
        "cover", "img", "pic", "goods_gallery_urls", "gallery", "images", "imgs",
        "goods_imgs", "goodsImgs", "carousel", "banner"
    }
    detail_keys = {
        "detail_gallery", "detailGallery", "detail_images", "detailImages", "desc_images",
        "descImgs", "descriptionImages", "rich_text_images", "goods_desc_images"
    }

    for obj in _walk(raw):
        for key, val in obj.items():
            low = str(key)
            bucket = None
            if low in main_keys or any(x in low.lower() for x in ("thumb", "image", "gallery", "carousel")):
                bucket = main_urls
            if low in detail_keys or any(x in low.lower() for x in ("detail", "desc", "richtext")):
                bucket = detail_urls
            if bucket is None:
                continue

            if isinstance(val, str):
                if re.search(r"\.(jpg|jpeg|png|webp)", val, flags=re.I) or "pdd" in val or "yangkeduo" in val:
                    bucket.append(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        bucket.append(item)
                    elif isinstance(item, dict):
                        for sub_key in ("url", "image_url", "imageUrl", "thumb_url", "src", "hd_url"):
                            if item.get(sub_key):
                                bucket.append(item[sub_key])
                                break
            elif isinstance(val, dict):
                for sub_val in val.values():
                    if isinstance(sub_val, str):
                        bucket.append(sub_val)

    return _dedupe(main_urls)[:20], _dedupe(detail_urls)[:40]


def extract_sku_list_from_raw(raw: Any) -> list[dict[str, Any]]:
    skus: list[dict[str, Any]] = []

    def parse_specs(obj: dict[str, Any]) -> tuple[str, str]:
        candidates = []
        for key in ("spec", "specs", "specList", "properties", "attrs", "sku_attrs", "thumbSpec", "name", "skuName"):
            val = obj.get(key)
            if val:
                candidates.append(val)

        names: list[str] = []
        for val in candidates:
            if isinstance(val, str):
                names.append(val)
            elif isinstance(val, list):
                for x in val:
                    if isinstance(x, str):
                        names.append(x)
                    elif isinstance(x, dict):
                        n = x.get("name") or x.get("value") or x.get("spec_value") or x.get("value_name") or x.get("spec")
                        if n:
                            names.append(str(n))
            elif isinstance(val, dict):
                for _, v in val.items():
                    if isinstance(v, (str, int, float)):
                        names.append(str(v))
        names = [_clean_text(x, 80) for x in names if _clean_text(x, 80)]
        return (names[0] if names else "默认", names[1] if len(names) > 1 else "")

    for obj in _walk(raw):
        # sku 列表通常在这些字段里。
        for field in ("sku", "skus", "sku_list", "skuList", "goodsSku", "goods_sku", "specs", "specList"):
            val = obj.get(field)
            if not isinstance(val, list):
                continue
            for row in val:
                if not isinstance(row, dict):
                    continue
                joined_keys = " ".join(row.keys()).lower()
                if not any(k in joined_keys for k in ("price", "sku", "stock", "quantity", "thumb", "spec")):
                    continue
                spec1, spec2 = parse_specs(row)
                image = (
                    row.get("thumb_url") or row.get("thumbUrl") or row.get("image_url") or row.get("imageUrl")
                    or row.get("hd_thumb_url") or row.get("hdThumbUrl") or row.get("url") or ""
                )
                price = (
                    row.get("price") or row.get("group_price") or row.get("groupPrice") or row.get("normal_price")
                    or row.get("normalPrice") or row.get("min_group_price") or row.get("salePrice") or 0
                )
                stock = row.get("stock") or row.get("quantity") or row.get("inventory") or row.get("sku_quantity") or 1000
                sku = {
                    "spec1": spec1 or "默认",
                    "spec2": spec2,
                    "price": _safe_price(price),
                    "stock": stock,
                    "sku_image_url": _normalize_image(image),
                    "merchant_sku": row.get("outer_id") or row.get("outerId") or row.get("sku_id") or row.get("skuId") or "",
                    "barcode": row.get("barcode") or "",
                    "raw": row,
                }
                skus.append(sku)

    # 去重：规格名+价格+图片。
    seen = set()
    out = []
    for sku in skus:
        key = (sku.get("spec1"), sku.get("spec2"), sku.get("price"), sku.get("sku_image_url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(sku)
    return out[:100]


def extract_services_from_raw(raw: Any) -> str:
    texts: list[str] = []
    for obj in _walk(raw):
        for key, val in obj.items():
            low = str(key).lower()
            if any(x in low for x in ("service", "promise", "after", "refund", "tag")):
                if isinstance(val, str):
                    texts.append(val)
                elif isinstance(val, list):
                    for x in val:
                        if isinstance(x, str):
                            texts.append(x)
                        elif isinstance(x, dict):
                            name = x.get("name") or x.get("text") or x.get("title") or x.get("label")
                            if name:
                                texts.append(str(name))
    joined = " ".join(_clean_text(x, 80) for x in texts)
    if "7" in joined and ("无理由" in joined or "退货" in joined):
        return "支持7天无理由退货(使用后不支持)"
    return _clean_text(joined, 200)


def extract_pdd_detail_from_page(tab) -> dict[str, Any]:
    """从当前商品详情页 DOM 中提取主图、详情图、规格、服务等可见信息。"""
    js = r"""
    try {
        const out = {title:'', price_text:'', attributes:{}, main_image_urls:[], detail_image_urls:[], sku_list:[], after_sale:''};
        const seen = new Set();
        function addImg(arr, v) {
            if (!v || typeof v !== 'string') return;
            if (v.startsWith('//')) v = location.protocol + v;
            if (!/^https?:\/\//.test(v)) return;
            if (!/(pdd|yangkeduo|pinduoduo|alicdn|tbcdn)/i.test(v)) return;
            if (seen.has(v)) return;
            seen.add(v); arr.push(v);
        }
        function txt(sel) {
            const el = document.querySelector(sel);
            return el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '';
        }
        out.title = txt('h1') || txt('[class*="title"]') || document.title.replace(/[-_|].*$/, '').trim();
        out.price_text = txt('[class*="price"]') || txt('[class*="Price"]');

        document.querySelectorAll('img').forEach(img => {
            const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
            const rect = img.getBoundingClientRect ? img.getBoundingClientRect() : {width:0,height:0};
            const alt = (img.alt || '').toLowerCase();
            if (rect.width > 250 || rect.height > 250 || /goods|商品|主图|轮播/.test(alt)) addImg(out.main_image_urls, src);
            if (/detail|详情|desc|rich|content/.test(img.outerHTML.toLowerCase()) || rect.height > 500) addImg(out.detail_image_urls, src);
        });

        const html = document.documentElement.innerHTML || '';
        const re = /(?:https?:)?\/\/[^"'\s<>]+(?:pdd|yangkeduo|pinduoduo)[^"'\s<>]+\.(?:jpg|jpeg|png|webp)/ig;
        let m;
        while ((m = re.exec(html)) !== null && out.main_image_urls.length < 80) addImg(out.main_image_urls, m[0]);

        // 属性：短文本 key:value。
        document.querySelectorAll('li, div, span, td').forEach(el => {
            const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
            if (!t || t.length > 120) return;
            if (!/[：:]/.test(t)) return;
            const parts = t.split(/[：:]/);
            if (parts.length >= 2 && parts[0].length <= 20) out.attributes[parts[0].trim()] = parts.slice(1).join(':').trim().slice(0, 80);
        });

        // SKU 规格块兜底。
        document.querySelectorAll('[class*="sku"], [class*="Sku"], [class*="spec"], [class*="Spec"]').forEach(el => {
            const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
            if (!t || t.length > 80 || /价格|库存|数量|选择/.test(t)) return;
            const img = el.querySelector && el.querySelector('img');
            const imgUrl = img ? (img.currentSrc || img.src || img.getAttribute('data-src') || '') : '';
            out.sku_list.push({spec1:t, spec2:'', price:0, stock:1000, sku_image_url:imgUrl});
        });

        const body = document.body ? document.body.innerText : '';
        if (/7天无理由|七天无理由/.test(body)) out.after_sale = '支持7天无理由退货(使用后不支持)';
        return JSON.stringify(out);
    } catch(e) { return '{}'; }
    """
    try:
        raw = tab.run_js(js) or "{}"
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enrich_pdd_product(item: dict[str, Any] | None, raw: Any = None, tab=None, logger=None) -> dict[str, Any] | None:
    if not item:
        return item

    raw_attrs = extract_attrs_from_raw(raw) if raw is not None else {}
    raw_main, raw_detail = extract_images_from_raw(raw) if raw is not None else ([], [])
    raw_skus = extract_sku_list_from_raw(raw) if raw is not None else []
    raw_service = extract_services_from_raw(raw) if raw is not None else ""

    page_data = extract_pdd_detail_from_page(tab) if tab is not None else {}

    attrs = item.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    attrs.update(raw_attrs)
    attrs.update(page_data.get("attributes") or {})
    item["attributes"] = attrs

    if page_data.get("title") and not item.get("title"):
        item["title"] = page_data["title"]
        item["original_title"] = page_data["title"]

    main_urls = []
    main_urls.extend(item.get("main_image_urls") or [])
    main_urls.extend(raw_main)
    main_urls.extend(page_data.get("main_image_urls") or [])
    main_urls.extend(item.get("image_urls") or [])
    item["main_image_urls"] = _dedupe(main_urls)[:20]

    detail_urls = []
    detail_urls.extend(item.get("detail_image_urls") or [])
    detail_urls.extend(raw_detail)
    detail_urls.extend(page_data.get("detail_image_urls") or [])
    item["detail_image_urls"] = _dedupe(detail_urls)[:40]

    skus = []
    skus.extend(item.get("sku_list") or [])
    skus.extend(raw_skus)
    skus.extend(page_data.get("sku_list") or [])
    if skus:
        item["sku_list"] = skus

    if raw_service or page_data.get("after_sale"):
        item["after_sale"] = raw_service or page_data.get("after_sale")

    # 平台默认库存；如果 SKU 提不到库存，也给导出表一个可编辑默认值。
    item.setdefault("stock", 1000)
    item.setdefault("platform", "pdd")
    return ensure_full_product_package(item)
