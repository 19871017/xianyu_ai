import uuid


def generate_license_key() -> str:
    """生成唯一License Key"""
    return uuid.uuid4().hex
