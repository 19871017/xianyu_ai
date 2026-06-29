"""License 签名校验（客户端侧）。

安全要点：
- 服务端用 RSA 私钥对 ``license_key:machine_id:expires_at`` 做 PSS+SHA256 签名。
- 客户端内嵌对应公钥，对每次接受的授权（在线 / 离线）强制验签。
- 私钥只在服务端，破解者无法伪造合法签名：
    * 改 verify() 直接返回 True —— 关键功能入口仍会要求有效签名令牌。
    * 把域名指向假服务器返回 {"valid": true} —— 假签名验签失败。
    * 伪造本地 license 文件 —— 同样过不了验签。

公钥是公开信息，内嵌客户端不构成泄密；真正的机密（私钥）始终留在服务端。
"""
from __future__ import annotations

# 服务端公钥（keys/public_key.pem）。仅用于验签，可公开。
LICENSE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAn1UL8fv9TJLCgRJRYmGI
Hoo5EXDGzcu6SpY+uvhVpm+eK2ePmo9g2rtJE8Gl6LFB00tYkoEcuM+xgNCnv6sD
178etFyowUVbQiqnphV7byBHVvpbPcJVwRfrElFuOYeXuJNll41cXL2WhcbZMDcN
s60ADZ/AwUmQ8ZoP9/sUxsMdGebPK0rN22dfvLZHE5C7cTM18dV8xkKtdRbY2zh7
I2WNkWf03ZhFYSfkJtfBf5PlnitM5Q29HsPl94l5VhhMrkrR/vsyOc0y49ahS0FQ
I9VNgvWftlgen/Yd7qT5TXR6xzzTm/Cmg7uGZ6ysrBV1brGwAEb3gnq5QprDjCte
hwIDAQAB
-----END PUBLIC KEY-----
"""

_public_key = None


def _load_public_key():
    global _public_key
    if _public_key is not None:
        return _public_key
    from cryptography.hazmat.primitives import serialization
    _public_key = serialization.load_pem_public_key(LICENSE_PUBLIC_KEY_PEM)
    return _public_key


def build_sign_payload(license_key: str, machine_id: str, expires_at: str) -> str:
    """构造与服务端完全一致的签名原文。"""
    return f"{license_key}:{machine_id}:{expires_at}"


def verify_license_signature(license_key: str, machine_id: str,
                             expires_at: str, signature_hex: str) -> bool:
    """RSA(PSS+SHA256) 公钥验签。任一环节异常即判失败。"""
    if not (license_key and machine_id and expires_at and signature_hex):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes
        payload = build_sign_payload(license_key, machine_id, expires_at)
        _load_public_key().verify(
            bytes.fromhex(signature_hex),
            payload.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
