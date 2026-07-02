"""能力守卫（方案B核心）：受控动作执行前强制向服务端换取签名令牌。

设计要点：
- 本模块与 license_validator/signature 同属 license 包，打包时被 Cython
  编译为原生扩展(.so/.pyd)，破解者要绕过必须改二进制而非改 .py 源码。
- engine 层核心入口（采集/上架/AI改写）调用 ``require_capability(action)``，
  拿不到有效令牌即抛 CapabilityError，令破解版即便 UI 显示"已激活"也调不动核心功能。
- 持有进程级 LicenseValidator 单例，避免改动 engine 各类的构造签名。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_validator = None


class CapabilityError(RuntimeError):
    """能力令牌获取失败：未授权 / 未联网 / 令牌无效。"""


def _get_validator():
    global _validator
    if _validator is None:
        with _lock:
            if _validator is None:
                from license.license_validator import LicenseValidator
                _validator = LicenseValidator()
    return _validator


def set_validator(validator) -> None:
    """允许 UI 注入已有的 validator 实例，复用其令牌缓存与登录态。"""
    global _validator
    with _lock:
        _validator = validator


def require_capability(action: str) -> None:
    """执行受控动作前调用；无有效服务端签名令牌则抛 CapabilityError。"""
    res = _get_validator().acquire_capability(action)
    if not res.get("ok"):
        raise CapabilityError(res.get("reason", "未获授权"))
