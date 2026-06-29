"""闲鱼商品擦亮器：到个人在售页点击「擦亮」，提升商品曝光。

闲鱼曝光高度依赖「擦亮」（把在架商品重新顶到搜索/列表前面）。本模块自动
遍历在售商品并点擦亮，解放手动操作。

安全护栏（最高优先级）：
  - 只点击擦亮/置顶类安全按钮（SAFE_REFRESH_TEXTS）。
  - 绝不点击下架/删除/编辑/降价等危险按钮（FORBIDDEN_TEXTS）。
  - 找不到擦亮入口时停下并回传原因，不盲点其它按钮。

可验证性：
  - is_safe_button / is_forbidden_button 为纯函数，可单测。
  - refresh_all 依赖浏览器与在架商品，开通在售商品后实测；当前账号无在架
    商品时会明确返回「未找到可擦亮商品」，不误点。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from config import PLATFORM_URLS
from utils.login_manager import ensure_login


PROFILE_URL = PLATFORM_URLS["xianyu"].get("profile", "https://www.goofish.com/personal")

# 只允许点击的擦亮类安全按钮文本。
SAFE_REFRESH_TEXTS = ("擦亮", "一键擦亮", "重新擦亮")

# 绝不点击的危险按钮文本（不可逆/影响在架状态）。
FORBIDDEN_TEXTS = (
    "删除", "下架", "编辑", "降价", "修改", "删除商品",
    "下架商品", "立即降价", "一口价", "出售", "拍卖",
)


def is_forbidden_button(text: str) -> bool:
    """判断按钮文本是否属于禁止点击的危险类。"""
    t = (text or "").strip()
    return any(f in t for f in FORBIDDEN_TEXTS)


def is_safe_button(text: str) -> bool:
    """判断按钮文本是否属于允许点击的擦亮类（且不含危险词）。"""
    t = (text or "").strip()
    if is_forbidden_button(t):
        return False
    return any(s in t for s in SAFE_REFRESH_TEXTS)


class XianyuRefresher:
    """闲鱼商品擦亮器（带安全护栏，绝不下架/删除）。"""

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

    def _goto_profile(self):
        self.tab.get(PROFILE_URL)
        time.sleep(5)
        # 滚动加载更多在售商品。
        for _ in range(6):
            try:
                self.tab.scroll.down(900)
            except Exception:
                pass
            time.sleep(0.6)

    def _find_refresh_buttons(self) -> int:
        """统计页面上可见的擦亮按钮数量（不点击）。"""
        find_js = r"""
        function vis(el){ if(!el) return false; var r=el.getBoundingClientRect();
          return r.width>0 && r.height>0; }
        var safe = arguments[0];
        var nodes = document.querySelectorAll('button, a, span, div');
        var n = 0;
        for (var i=0;i<nodes.length;i++){
          var t = (nodes[i].innerText||'').trim();
          if (!t || t.length>8) continue;
          for (var j=0;j<safe.length;j++){
            if (t.indexOf(safe[j])>=0 && vis(nodes[i])) { n++; break; }
          }
        }
        return n;
        """
        try:
            return int(self.tab.run_js(find_js, list(SAFE_REFRESH_TEXTS)) or 0)
        except Exception:
            return 0

    def refresh_all(self, max_items: int = 50) -> dict[str, Any]:
        """遍历在售商品点擦亮。返回 {ok, refreshed, found, note}。

        安全：每次点击前用文本判定，命中危险词一律跳过；只点擦亮类按钮。
        """
        out = {"ok": False, "refreshed": 0, "found": 0, "note": ""}
        if not self.tab:
            out["note"] = "浏览器未就绪，请先 open()"
            return out

        try:
            self._goto_profile()
        except Exception as e:
            out["note"] = f"打开个人在售页异常: {e}"
            return out

        found = self._find_refresh_buttons()
        out["found"] = found
        if found <= 0:
            out["note"] = "未找到可擦亮商品（可能无在架商品或页面改版）。"
            self.log(out["note"])
            return out

        self.log(f"发现 {found} 个擦亮入口，开始逐个擦亮（带安全护栏）…")
        refreshed = 0
        # 逐个点击：每次重新查找首个未处理的擦亮按钮（点击后 DOM 会变）。
        click_js = r"""
        function vis(el){ if(!el) return false; var r=el.getBoundingClientRect();
          return r.width>0 && r.height>0; }
        var safe = arguments[0], forbidden = arguments[1];
        var nodes = document.querySelectorAll('button, a, span, div');
        for (var i=0;i<nodes.length;i++){
          var t = (nodes[i].innerText||'').trim();
          if (!t || t.length>8) continue;
          var bad = false;
          for (var k=0;k<forbidden.length;k++){ if (t.indexOf(forbidden[k])>=0){ bad=true; break; } }
          if (bad) continue;
          for (var j=0;j<safe.length;j++){
            if (t.indexOf(safe[j])>=0 && vis(nodes[i]) && !nodes[i].__rdone){
              nodes[i].__rdone = true;
              nodes[i].click();
              return t;
            }
          }
        }
        return "";
        """
        for _ in range(min(max_items, found)):
            try:
                clicked = self.tab.run_js(click_js, list(SAFE_REFRESH_TEXTS), list(FORBIDDEN_TEXTS))
            except Exception as e:
                self.log(f"擦亮点击异常: {e}")
                break
            if not clicked:
                break
            refreshed += 1
            self.log(f"  ✓ 已擦亮（{refreshed}）：{clicked}")
            time.sleep(1.2)

        out["refreshed"] = refreshed
        out["ok"] = refreshed > 0
        out["note"] = f"完成擦亮 {refreshed}/{found} 个。" if refreshed else "未能点击擦亮按钮。"
        return out
