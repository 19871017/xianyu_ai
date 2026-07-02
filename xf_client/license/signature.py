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
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA+M0DYnLxqP8zmQZo5L11
oz8f0l/uOSfu1Or480NgNKBgEe4m+iCRw66Ev4K34Inn+KIvwyo4fWDW1PjWItWW
EeDwTp5yzqdIrD9Qf8DvY9TUz1mKRgMHs4roZ4vjYxfLTg+/zFZtEtBLqDFoFO/O
IFMR9YXWtcYsjaTz1CjGMWST+1OrEy3IfAwL4X1nfIgDFvX3Lp5SSaqKcvVanpb4
OKNHbYAR/09FxjxIMk6ONHZjVU+dND7IHkuaKsusyL3JLzI0bO0xDlry1K3oFFtK
DQfdQiaYhMfgIUamirSm/gfCoiiqj6M66jDShzFUQjA9XUmh0dX1FYYnJlwJNejD
BQIDAQAB
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


def build_capability_payload(action: str, machine_id: str, expire_ts) -> str:
    """构造与服务端完全一致的能力令牌签名原文。"""
    return f"cap:{action}:{machine_id}:{expire_ts}"


def verify_capability_token(action: str, machine_id: str, expire_ts,
                            token_hex: str) -> bool:
    """校验服务端下发的能力令牌（RSA-PSS 公钥验签）。

    方案B核心：采集/上架/AI改写等动作执行前，客户端须持有服务端签名的
    短期令牌。私钥只在服务端，破解版伪造不出合法令牌。任一环节异常即判失败。
    """
    if not (action and machine_id and expire_ts and token_hex):
        return False
    # 过期即失效：签名保证攻击者伪造不出未来的到期时间，此处再拦截过期令牌重放。
    try:
        import time as _t
        if int(expire_ts) < int(_t.time()):
            return False
    except Exception:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes
        payload = build_capability_payload(action, machine_id, expire_ts)
        _load_public_key().verify(
            bytes.fromhex(token_hex),
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
