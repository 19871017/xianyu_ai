"""闲管家(goofish.pro) 发布页 DOM 取样脚本（基于统一登录模块）。

用法：
    cd ~/Desktop/xianyu_ai/xf_client
    ../.venv/bin/python tools_dump_goofishpro_auto.py

特点：
- 复用 utils.login_manager.ensure_login：会“一直等你登录成功”再继续，
  不会抓完即走。登录态保存后下次免登录。
- 登录成功后打开发布页，抓取 HTML + 结构化表单摘要。
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import PLATFORM_URLS
from utils.login_manager import ensure_login

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(HERE, "dump_goofishpro.html")
OUT_FORM = os.path.join(HERE, "dump_goofishpro_form.json")
PUBLISH_URL = PLATFORM_URLS["goofishpro"]["publish"]

FORM_PROBE_JS = r"""
(function () {
  function brief(el) {
    if (!el) return null;
    return {
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      cls: (el.getAttribute('class') || '').slice(0, 120),
      text: (el.innerText || '').trim().slice(0, 40)
    };
  }
  var out = {url: location.href, title: document.title,
    inputs: [], textareas: [], selects: [], buttons: [], uploads: [], sku_hints: [], labels: []};
  document.querySelectorAll('input').forEach(function (e) {
    var b = brief(e);
    if (e.type === 'file') out.uploads.push(b); else out.inputs.push(b);
  });
  document.querySelectorAll('textarea').forEach(function (e) { out.textareas.push(brief(e)); });
  document.querySelectorAll('select').forEach(function (e) { out.selects.push(brief(e)); });
  document.querySelectorAll('button, [class*="btn"], [role="button"]').forEach(function (e) {
    var b = brief(e); if (b && b.text) out.buttons.push(b);
  });
  var skuSel = '[class*="sku"], [class*="Sku"], [class*="spec"], [class*="Spec"]';
  document.querySelectorAll(skuSel).forEach(function (e) { var b = brief(e); if (b) out.sku_hints.push(b); });
  document.querySelectorAll('label, [class*="label"], [class*="form-item"] [class*="title"]').forEach(function (e) {
    var t = (e.innerText || '').trim(); if (t && t.length < 30) out.labels.push(t);
  });
  out.labels = Array.from(new Set(out.labels)).slice(0, 80);
  out.buttons = out.buttons.slice(0, 60);
  out.sku_hints = out.sku_hints.slice(0, 80);
  return JSON.stringify(out);
})();
"""


def log(msg):
    print(msg, flush=True)


def main():
    # 1) 确保登录（会一直等到登录成功）
    res = ensure_login("goofishpro", on_log=log, timeout=600)
    if not res["ok"]:
        log(f"!! 登录失败: {res.get('error')}")
        # 仍尝试关闭浏览器
        if res.get("browser"):
            try:
                res["browser"].quit()
            except Exception:
                pass
        return

    browser = res["browser"]
    tab = res["tab"]

    # 2) 打开发布页
    log(f">> 打开发布页: {PUBLISH_URL}")
    tab.get(PUBLISH_URL)
    time.sleep(6)
    for _ in range(6):
        try:
            tab.scroll.down(800)
        except Exception:
            pass
        time.sleep(0.6)
    time.sleep(2)

    html = tab.html or ""
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    log(f">> 当前 URL: {(tab.url or '')[:100]}")
    log(f">> 当前标题: {tab.title}")
    log(f">> HTML 已保存: {OUT_HTML} ({len(html)} 字符)")

    try:
        raw = tab.run_js(FORM_PROBE_JS)
        form = json.loads(raw) if isinstance(raw, str) else {}
    except Exception as e:
        form = {"error": str(e)}
    with open(OUT_FORM, "w", encoding="utf-8") as f:
        json.dump(form, f, ensure_ascii=False, indent=2)
    log(f">> 表单摘要已保存: {OUT_FORM}")
    if isinstance(form, dict) and "inputs" in form:
        log(f"   输入框 {len(form.get('inputs', []))}  文本域 {len(form.get('textareas', []))}  "
            f"下拉 {len(form.get('selects', []))}  上传 {len(form.get('uploads', []))}  "
            f"按钮 {len(form.get('buttons', []))}  SKU线索 {len(form.get('sku_hints', []))}")
        log(f"   字段标签: {form.get('labels', [])[:40]}")

    try:
        browser.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
