"""LicenseValidator 行为测试：在线判失效不回退、离线宽限、机器码绑定。"""
import os
import sys
import json
import time
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class LicenseValidatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lic_file = os.path.join(self.tmp, ".xf_license.json")
        # patch config 常量
        import config
        self._orig_file = config.LICENSE_FILE
        config.LICENSE_FILE = self.lic_file
        from license import license_validator as lv
        lv.LICENSE_FILE = self.lic_file
        self.lv = lv

    def tearDown(self):
        import config
        config.LICENSE_FILE = self._orig_file

    def _make(self, data):
        with open(self.lic_file, "w") as f:
            json.dump(data, f)
        v = self.lv.LicenseValidator()
        v.machine_id = "MID"
        return v

    def test_not_activated(self):
        v = self.lv.LicenseValidator()
        v.license_data = {}
        self.assertFalse(v.verify()["valid"])

    def test_server_invalid_no_local_fallback(self):
        v = self._make({"license_key": "k", "machine_id": "MID",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": int(time.time())})
        with mock.patch.object(self.lv.requests, "get") as g:
            # 第一次 / 探测可达，第二次 verify 返回 invalid
            g.side_effect = [
                _Resp(200, {}),
                _Resp(200, {"valid": False, "reason": "该设备已被管理员强制下线"}),
            ]
            res = v.verify()
        self.assertFalse(res["valid"])
        self.assertIn("强制下线", res["reason"])

    def test_offline_within_grace(self):
        v = self._make({"license_key": "k", "machine_id": "MID", "signature": "deadbeef",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": int(time.time())})
        with mock.patch.object(self.lv.requests, "get") as g, \
                mock.patch.object(self.lv, "verify_license_signature", return_value=True):
            g.return_value = _Resp(0, {})
            g.side_effect = self.lv.requests.exceptions.ConnectionError()
            res = v.verify()
        self.assertTrue(res["valid"])
        self.assertEqual(res.get("source"), "offline")

    def test_offline_bad_signature_rejected(self):
        # 离线宽限内、机器码匹配、未过期，但签名无效 -> 必须判失效（防伪造本地文件）
        v = self._make({"license_key": "k", "machine_id": "MID", "signature": "bad",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": int(time.time())})
        with mock.patch.object(self.lv.requests, "get") as g, \
                mock.patch.object(self.lv, "verify_license_signature", return_value=False):
            g.side_effect = self.lv.requests.exceptions.ConnectionError()
            res = v.verify()
        self.assertFalse(res["valid"])
        self.assertIn("签名", res["reason"])

    def test_online_bad_signature_rejected(self):
        # 服务端返回 valid:true 但签名验不过（假服务器）-> 判失效
        v = self._make({"license_key": "k", "machine_id": "MID", "signature": "bad",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": int(time.time())})
        with mock.patch.object(self.lv.requests, "get") as g, \
                mock.patch.object(self.lv, "verify_license_signature", return_value=False):
            g.side_effect = [
                _Resp(200, {}),
                _Resp(200, {"valid": True, "expires_at": "2999-01-01T00:00:00", "signature": "bad"}),
            ]
            res = v.verify()
        self.assertFalse(res["valid"])
        self.assertIn("签名", res["reason"])

    def test_offline_exceeds_grace(self):
        old = int(time.time()) - (10 * 24 * 3600)
        v = self._make({"license_key": "k", "machine_id": "MID",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": old})
        with mock.patch.object(self.lv.requests, "get") as g:
            g.side_effect = self.lv.requests.exceptions.ConnectionError()
            res = v.verify()
        self.assertFalse(res["valid"])

    def test_offline_machine_mismatch(self):
        v = self._make({"license_key": "k", "machine_id": "OTHER",
                        "expires_at": "2999-01-01T00:00:00", "last_online_verify": int(time.time())})
        with mock.patch.object(self.lv.requests, "get") as g:
            g.side_effect = self.lv.requests.exceptions.ConnectionError()
            res = v.verify()
        self.assertFalse(res["valid"])
        self.assertIn("机器码", res["reason"])


if __name__ == "__main__":
    unittest.main()
