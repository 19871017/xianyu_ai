"""客户端更新检测 回归测试（纯逻辑 + mock 网络，离线可重复）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import update_checker as uc


class TestParseVersion(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(uc.parse_version("3.2.0"), (3, 2, 0))

    def test_mixed_separators(self):
        self.assertEqual(uc.parse_version("3_2-1"), (3, 2, 1))

    def test_non_numeric_segment_zero(self):
        self.assertEqual(uc.parse_version("3.2.0beta"), (3, 2, 0))

    def test_empty(self):
        self.assertEqual(uc.parse_version(""), (0,))


class TestIsNewer(unittest.TestCase):
    def test_patch_newer(self):
        self.assertTrue(uc.is_newer("3.2.1", "3.2.0"))

    def test_minor_newer(self):
        self.assertTrue(uc.is_newer("3.3.0", "3.2.9"))

    def test_equal_not_newer(self):
        self.assertFalse(uc.is_newer("3.2.0", "3.2.0"))

    def test_older_not_newer(self):
        self.assertFalse(uc.is_newer("3.1.9", "3.2.0"))

    def test_shorter_padded(self):
        # 3.2 == 3.2.0，不算更新
        self.assertFalse(uc.is_newer("3.2", "3.2.0"))
        self.assertTrue(uc.is_newer("3.2.1", "3.2"))


class TestCheckUpdate(unittest.TestCase):
    def test_no_latest_returns_no_update(self):
        with mock.patch.object(uc, "fetch_latest", return_value=None):
            r = uc.check_update("3.2.0")
        self.assertFalse(r["has_update"])
        self.assertEqual(r["download_url"], uc.DOWNLOAD_SITE_URL)

    def test_same_version_no_update(self):
        with mock.patch.object(uc, "fetch_latest",
                               return_value={"version": "3.2.0", "download_url": "/downloads/x.dmg"}):
            r = uc.check_update("3.2.0")
        self.assertFalse(r["has_update"])

    def test_newer_version_triggers_update(self):
        latest = {"version": "3.3.0", "download_url": "/downloads/mac_3.3.0.dmg",
                  "force_update": True, "release_notes": "修了若干 bug"}
        with mock.patch.object(uc, "fetch_latest", return_value=latest):
            r = uc.check_update("3.2.0")
        self.assertTrue(r["has_update"])
        self.assertTrue(r["force_update"])
        self.assertEqual(r["version"], "3.3.0")
        # 相对路径补全为下载站绝对地址
        self.assertTrue(r["download_url"].endswith("/downloads/mac_3.3.0.dmg"))
        self.assertIn("修了若干 bug", r["notes"])

    def test_absolute_url_kept(self):
        latest = {"version": "3.3.0", "download_url": "https://cdn.x/app.dmg"}
        with mock.patch.object(uc, "fetch_latest", return_value=latest):
            r = uc.check_update("3.2.0")
        self.assertEqual(r["download_url"], "https://cdn.x/app.dmg")

    def test_empty_url_falls_back_to_site(self):
        latest = {"version": "3.3.0", "download_url": ""}
        with mock.patch.object(uc, "fetch_latest", return_value=latest):
            r = uc.check_update("3.2.0")
        self.assertEqual(r["download_url"], uc.DOWNLOAD_SITE_URL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
