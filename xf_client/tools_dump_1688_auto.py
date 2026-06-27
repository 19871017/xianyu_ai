"""1688 详情页取样脚本（全自动版，复用已登录 profile，无需交互）。

用法：
    cd ~/Desktop/xianyu_ai/xf_client
    ../.venv/bin/python tools_dump_1688_auto.py "商品链接"
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DrissionPage import Chromium
from utils.browser_config import get_chromium_options, check_browser_available
from engine.alibaba_sku_parser import parse_sku_from_html, extract_init_json, _find_sku_model

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".xf_1688_profile")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(HERE, "dump_1688.html")
OUT_SKUS = os.path.join(HERE, "dump_1688_skus.json")


def _grab_detail_tab(browser, target_id):
    best = None
    for tab in browser.get_tabs():
        try:
            url = tab.url or ""
        except Exception:
            continue
        if target_id and target_id in url:
            return tab
        if "detail.1688.com" in url:
            best = tab
    return best or browser.latest_tab


def main():
    if len(sys.argv) < 2:
        print("用法: python tools_dump_1688_auto.py \"1688商品链接\"")
        return
    url = sys.argv[1]
    target_id = "".join(c for c in url.split("offer/")[-1].split(".")[0] if c.isdigit()) if "offer/" in url else ""

    ok, msg = check_browser_available()
    if not ok:
        print(f"浏览器检查失败: {msg}")
        return

    os.makedirs(PROFILE_DIR, exist_ok=True)
    co, _ = get_chromium_options(user_data_dir=PROFILE_DIR)
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1440,900")

    browser = Chromium(co)
    tab = browser.latest_tab

    print(f">> 打开商品详情页：{url[:90]}…")
    tab.get(url)
    time.sleep(7)

    tab = _grab_detail_tab(browser, target_id)
    try:
        tab.set.activate()
    except Exception:
        pass

    for _ in range(8):
        try:
            tab.scroll.down(900)
        except Exception:
            pass
        time.sleep(0.8)
    time.sleep(2)

    cur = tab.url or ""
    title = ""
    try:
        title = tab.title or ""
    except Exception:
        pass
    html = tab.html or ""
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f">> 当前标签 URL：{cur[:90]}")
    print(f">> 当前标签标题：{title}")
    print(f">> 已保存 HTML：{OUT_HTML}  （{len(html)} 字符）")

    if target_id and target_id not in html and target_id not in cur:
        print(f"!! 警告：HTML 里没找到目标商品 ID {target_id}，可能未登录/被风控拦截。")

    data = extract_init_json(html)
    print(f">> 内嵌 JSON 命中：{'是' if data else '否'}")
    if data:
        model = _find_sku_model(data)
        print(f">> skuModel 命中：{'是' if model else '否'}")

    skus = parse_sku_from_html(html)
    with open(OUT_SKUS, "w", encoding="utf-8") as f:
        json.dump(skus, f, ensure_ascii=False, indent=2)
    print(f"\n>> 解析到 SKU 数量：{len(skus)}")
    for s in skus[:10]:
        print(f"   - {s.get('spec1')} / {s.get('spec2')}  ¥{s.get('price')}  库存{s.get('stock')}")
    print(f">> SKU 明细已存：{OUT_SKUS}")

    try:
        browser.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
