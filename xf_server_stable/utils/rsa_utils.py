import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from config import RSA_KEY_SIZE, RSA_PRIVATE_KEY_PATH, RSA_PUBLIC_KEY_PATH

_private_key = None
_public_key = None


def ensure_keys():
    """确保RSA密钥对存在，不存在则生成"""
    global _private_key, _public_key
    os.makedirs(os.path.dirname(RSA_PRIVATE_KEY_PATH), exist_ok=True)
    if not os.path.exists(RSA_PRIVATE_KEY_PATH) or not os.path.exists(RSA_PUBLIC_KEY_PATH):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=RSA_KEY_SIZE,
        )
        with open(RSA_PRIVATE_KEY_PATH, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ))
        public_key = private_key.public_key()
        with open(RSA_PUBLIC_KEY_PATH, "wb") as f:
            f.write(public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ))
        os.chmod(RSA_PRIVATE_KEY_PATH, 0o600)
    # 加载到内存
    with open(RSA_PRIVATE_KEY_PATH, "rb") as f:
        _private_key = serialization.load_pem_private_key(f.read(), password=None)
    with open(RSA_PUBLIC_KEY_PATH, "rb") as f:
        _public_key = serialization.load_pem_public_key(f.read())


def sign_data(data: str) -> str:
    """RSA私钥签名"""
    global _private_key
    if _private_key is None:
        ensure_keys()
    signature = _private_key.sign(
        data.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return signature.hex()


def verify_signature(data: str, signature_hex: str) -> bool:
    """RSA公钥验签"""
    global _public_key
    if _public_key is None:
        ensure_keys()
    try:
        _public_key.verify(
            bytes.fromhex(signature_hex),
            data.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False
