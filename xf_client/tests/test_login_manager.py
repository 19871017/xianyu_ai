"""统一登录/Cookie 管理单测（用假 tab，无浏览器依赖）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import login_manager as lm


class FakeCookies(list):
    """模拟 DrissionPage CookiesList：可迭代出 dict，并有 as_dict。"""
    def as_dict(self):
        return {c["name"]: c.get("value", "") for c in self}


class FakeSet:
    def __init__(self, tab):
        self._tab = tab
    def cookies(self, c):
        self._tab.injected.append(c)


class FakeTab:
    def __init__(self, url="", cookies=None):
        self._url = url
        self._cookies = FakeCookies(cookies or [])
        self.injected = []
        self.set = FakeSet(self)
    @property
    def url(self):
        return self._url
    def get(self, url):
        self._url = url
    def cookies(self):
        return self._cookies


class TestLoginDetection(unittest.TestCase):
    def test_on_login_page_blocks(self):
        tab = FakeTab(url="https://login.1688.com/x", cookies=[{"name": "unb", "value": "1"}])
        self.assertFalse(lm.is_logged_in(tab, "1688"))

    def test_logged_in_by_cookie(self):
        tab = FakeTab(url="https://www.1688.com/", cookies=[
            {"name": "unb", "value": "12345"}, {"name": "cookie17", "value": "abc"}])
        self.assertTrue(lm.is_logged_in(tab, "1688"))

    def test_not_logged_in_without_cookie(self):
        tab = FakeTab(url="https://www.1688.com/", cookies=[{"name": "foo", "value": "bar"}])
        self.assertFalse(lm.is_logged_in(tab, "1688"))

    def test_goofishpro_generic(self):
        tab = FakeTab(url="https://goofish.pro/sale/product/add", cookies=[{"name": "token", "value": "t"}])
        self.assertTrue(lm.is_logged_in(tab, "goofishpro"))
        tab2 = FakeTab(url="https://goofish.pro/login", cookies=[{"name": "token", "value": "t"}])
        self.assertFalse(lm.is_logged_in(tab2, "goofishpro"))


class TestCookiePersistence(unittest.TestCase):
    def setUp(self):
        # 隔离到临时目录，且禁用 db 写入
        self._orig_dir = lm.COOKIE_DIR
        self._orig_db = lm._db
        import tempfile
        lm.COOKIE_DIR = tempfile.mkdtemp(prefix="xf_cookie_test_")
        lm._db = None

    def tearDown(self):
        lm.COOKIE_DIR = self._orig_dir
        lm._db = self._db if hasattr(self, "_db") else self._orig_db
        lm._db = self._orig_db

    def test_save_and_load_roundtrip(self):
        cookies = [{"name": "unb", "value": "999"}, {"name": "cookie17", "value": "xyz"}]
        lm.save_cookies("1688", cookies, extra={"profile": "/tmp/x"})
        loaded = lm.load_cookies("1688")
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["name"], "unb")

    def test_clear(self):
        lm.save_cookies("jd", [{"name": "pin", "value": "u"}])
        self.assertTrue(lm.load_cookies("jd"))
        lm.clear_cookies("jd")
        self.assertEqual(lm.load_cookies("jd"), [])

    def test_inject_counts(self):
        tab = FakeTab(url="https://x")
        n = lm._inject_cookies(tab, [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}])
        self.assertEqual(n, 2)
        self.assertEqual(len(tab.injected), 2)

    def test_read_tab_cookies(self):
        tab = FakeTab(url="https://x", cookies=[{"name": "a", "value": "1"}])
        got = lm._read_tab_cookies(tab)
        self.assertEqual(got[0]["name"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class _FakeStorageTab:
    """模拟 SPA：无 Cookie，登录态在 localStorage。"""
    def __init__(self, url, storage):
        self._url = url
        self._storage = storage or {}

    @property
    def url(self):
        return self._url

    def cookies(self):
        return FakeCookies([])

    def run_js(self, js, *args):
        if "localStorage.length" in js:
            import json as _json
            return _json.dumps(self._storage)
        return ""


class TestStorageLogin(unittest.TestCase):
    def test_logged_in_by_access_token(self):
        tab = _FakeStorageTab("https://goofish.pro/sale/statistics",
                              {"access_token": "eyJhbGci.xxx.yyy"})
        self.assertTrue(lm.is_logged_in(tab, "goofishpro"))

    def test_not_logged_in_empty_storage(self):
        tab = _FakeStorageTab("https://goofish.pro/sale/statistics", {})
        self.assertFalse(lm.is_logged_in(tab, "goofishpro"))

    def test_undefined_token_not_logged_in(self):
        tab = _FakeStorageTab("https://goofish.pro/sale/statistics",
                              {"access_token": "undefined"})
        self.assertFalse(lm.is_logged_in(tab, "goofishpro"))

    def test_login_page_blocks_even_with_token(self):
        tab = _FakeStorageTab("https://goofish.pro/login",
                              {"access_token": "eyJhbGci.xxx"})
        self.assertFalse(lm.is_logged_in(tab, "goofishpro"))
