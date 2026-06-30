"""配置域名/密钥编码隐藏 回归测试。

确保 config.py 的 XOR+base64 编码值能正确还原为预期的服务端地址与客户端密钥，
防止以后改动编码逻辑或编码值时悄悄破坏线上连接（域名/密钥错误会导致全员激活失败）。
不联网，纯解码校验。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestConfigObfuscation(unittest.TestCase):
    def setUp(self):
        # 清掉可能存在的环境变量覆盖，确保校验的是内置编码值。
        for k in ("XF_SERVER_BASE_URL", "XF_CLIENT_API_KEY", "XF_DOWNLOAD_SITE_URL"):
            os.environ.pop(k, None)
        import importlib
        import config
        importlib.reload(config)
        self.config = config

    def test_server_base_url(self):
        self.assertEqual(self.config.SERVER_BASE_URL, "https://xy.lxd997.dpdns.org")

    def test_client_api_key(self):
        self.assertEqual(
            self.config.CLIENT_API_KEY,
            "a5008d5e75e902a25cde6f3e72181d25ed9967471e8d2545540bf624a6f39626",
        )

    def test_api_endpoints_built_from_base(self):
        self.assertEqual(
            self.config.API_LICENSE_VERIFY,
            "https://xy.lxd997.dpdns.org/api/license/verify",
        )
        self.assertEqual(
            self.config.DOWNLOAD_SITE_URL,
            "https://xy.lxd997.dpdns.org/",
        )

    def test_no_plaintext_domain_in_source(self):
        # 源码里不应再出现明文域名（除测试自身外）。
        here = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(os.path.dirname(here), "config.py")
        with open(cfg_path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("dpdns.org", src)

    def test_env_override_takes_precedence(self):
        os.environ["XF_SERVER_BASE_URL"] = "https://example.test"
        import importlib
        import config
        importlib.reload(config)
        self.assertEqual(config.SERVER_BASE_URL, "https://example.test")
        # 还原，避免污染其它测试
        os.environ.pop("XF_SERVER_BASE_URL", None)
        importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
