"""能力令牌验签单测（方案B核心纯逻辑）。

用临时 RSA 密钥对模拟「服务端私钥签名 → 客户端内嵌公钥验签」的闭环：
  - 合法令牌验签通过。
  - 篡改 action / machine_id / 到期时间 → 验签失败。
  - 令牌已过期 → 即便签名正确也失败（防重放）。
  - 假签名 / 空参数 → 失败。
  - 客户端 payload 格式与服务端签名原文完全一致（cap:action:machine_id:expire_ts）。

私钥只在服务端，破解版客户端拿不到私钥便伪造不出合法令牌——这是方案B
让盗版「即便 UI 显示已激活也调不动核心功能」的根本保证。
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding

from license import signature


def _sign(private_key, payload: str) -> str:
    sig = private_key.sign(
        payload.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return sig.hex()


class TestCapabilityToken(unittest.TestCase):
    def setUp(self):
        # 生成临时密钥对，替换客户端内嵌公钥（测试后还原）。
        self._priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._saved = signature._public_key
        signature._public_key = self._priv.public_key()
        self.machine_id = "abc123machine"
        self.future = int(time.time()) + 300

    def tearDown(self):
        signature._public_key = self._saved

    def _token(self, action, machine_id, expire_ts):
        payload = signature.build_capability_payload(action, machine_id, expire_ts)
        return _sign(self._priv, payload)

    def test_valid_token_passes(self):
        tok = self._token("collect", self.machine_id, self.future)
        self.assertTrue(signature.verify_capability_token(
            "collect", self.machine_id, self.future, tok))

    def test_payload_format_matches_server(self):
        # 与服务端 issue_capability 的签名原文格式必须逐字一致。
        self.assertEqual(
            signature.build_capability_payload("listing", "m1", 1700000000),
            "cap:listing:m1:1700000000",
        )

    def test_tampered_action_fails(self):
        tok = self._token("collect", self.machine_id, self.future)
        self.assertFalse(signature.verify_capability_token(
            "listing", self.machine_id, self.future, tok))

    def test_tampered_machine_fails(self):
        tok = self._token("collect", self.machine_id, self.future)
        self.assertFalse(signature.verify_capability_token(
            "collect", "other-machine", self.future, tok))

    def test_tampered_expiry_fails(self):
        tok = self._token("collect", self.machine_id, self.future)
        self.assertFalse(signature.verify_capability_token(
            "collect", self.machine_id, self.future + 999, tok))

    def test_expired_token_fails(self):
        past = int(time.time()) - 10
        tok = self._token("collect", self.machine_id, past)
        self.assertFalse(signature.verify_capability_token(
            "collect", self.machine_id, past, tok))

    def test_fake_signature_fails(self):
        self.assertFalse(signature.verify_capability_token(
            "collect", self.machine_id, self.future, "deadbeef"))

    def test_empty_params_fail(self):
        self.assertFalse(signature.verify_capability_token("", "", 0, ""))
        self.assertFalse(signature.verify_capability_token(
            "collect", self.machine_id, self.future, ""))

    def test_wrong_key_fails(self):
        # 攻击者用自己的私钥签名，但客户端内嵌的是服务端公钥 → 验签失败。
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        payload = signature.build_capability_payload("collect", self.machine_id, self.future)
        bad = _sign(attacker, payload)
        self.assertFalse(signature.verify_capability_token(
            "collect", self.machine_id, self.future, bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
