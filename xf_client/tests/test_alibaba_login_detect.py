"""AlibabaCollector 登录检测单测（假 tab，无浏览器依赖）。

核心回归点：旧实现用 document.cookie 读 cookie17，但它是 httpOnly，
JS 永远读不到 → 登录成功也判 false → 一直等待。
新实现改用 DrissionPage tab.cookies()（含 httpOnly）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.alibaba_collector import AlibabaCollector


class FakeCookies(list):
    def as_dict(self):
        return {c["name"]: c.get("value", "") for c in self}


class FakeTab:
    """document.cookie 只返回非 httpOnly 的部分，cookies() 返回全部。"""

    def __init__(self, url="", cookies=None, js_cookie=""):
        self._url = url
        self._cookies = FakeCookies(cookies or [])
        self._js_cookie = js_cookie

    @property
    def url(self):
        return self._url

    def cookies(self):
        return self._cookies

    def run_js(self, js, *args):
        if "document.cookie" in js:
            return self._js_cookie
        return None


def _collector_with_tab(tab):
    c = AlibabaCollector()
    c.tab = tab
    return c


class TestAlibabaLoginDetect(unittest.TestCase):
    def test_login_page_blocks(self):
        tab = FakeTab(url="https://login.1688.com/member/signin.htm",
                      cookies=[{"name": "unb", "value": "12345"}])
        self.assertFalse(_collector_with_tab(tab)._is_logged_in())

    def test_taobao_login_page_blocks(self):
        tab = FakeTab(url="https://login.taobao.com/x",
                      cookies=[{"name": "unb", "value": "12345"}])
        self.assertFalse(_collector_with_tab(tab)._is_logged_in())

    def test_httponly_cookie17_detected(self):
        # 关键回归：cookie17 是 httpOnly，document.cookie 读不到，
        # 但 tab.cookies() 能读到 → 应判已登录。
        tab = FakeTab(url="https://www.1688.com/",
                      cookies=[{"name": "cookie17", "value": "abcdef"}],
                      js_cookie="")
        self.assertTrue(_collector_with_tab(tab)._is_logged_in())

    def test_unb_detected(self):
        tab = FakeTab(url="https://www.1688.com/",
                      cookies=[{"name": "unb", "value": "12345678"}])
        self.assertTrue(_collector_with_tab(tab)._is_logged_in())

    def test_no_cookie_not_logged_in(self):
        tab = FakeTab(url="https://www.1688.com/",
                      cookies=[{"name": "foo", "value": "bar"}])
        self.assertFalse(_collector_with_tab(tab)._is_logged_in())

    def test_empty_cookies_not_logged_in(self):
        tab = FakeTab(url="https://www.1688.com/", cookies=[])
        self.assertFalse(_collector_with_tab(tab)._is_logged_in())


if __name__ == "__main__":
    unittest.main(verbosity=2)
