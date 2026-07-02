"""能力守卫（方案B核心）：受控动作执行前强制向服务端换取签名令牌。

设计要点：
- 本模块与 license_validator/signature 同属 license 包，打包时被 Cython
  编译为原生扩展(.so/.pyd)，破解者要绕过必须改二进制而非改 .py 源码。
- engine 层核心入口（采集/上架/AI改写）调用 ``require_capability(action)``，
  拿不到有效令牌即抛 CapabilityError，令破解版即便 UI 显示"已激活"也调不动核心功能。
- 持有进程级 LicenseValidator 单例，避免改动 engine 各类的构造签名。

fail-closed 完整性校验：
- 分发包（PyInstaller frozen）中，核心安全模块必须是 Cython 原生扩展(.pyd/.so)。
- 一旦被误打成明文源码包（源码可读、验签公钥可被一行替换），核心功能一律拒用。
  这道校验把"误用普通打包发出可破解的明文包"从静默事故变成当场失败，逼迫重打加密版。
"""
from __future__ import annotations

import sys
import threading

_lock = threading.Lock()
_validator = None

# 分发包中必须为原生扩展的核心安全模块（明文任一即判非官方构建）。
_CORE_SECURITY_MODULES = (
    "license.signature",
    "license.license_validator",
    "license.capability_guard",
    "config",
)
_integrity_ok = None


class CapabilityError(RuntimeError):
    """能力令牌获取失败：未授权 / 未联网 / 令牌无效 / 非官方构建。"""


def _module_is_native(mod) -> bool:
    """模块是否由原生扩展(.pyd/.so)加载，而非纯 Python 源码/字节码。"""
    origin = ""
    spec = getattr(mod, "__spec__", None)
    if spec is not None:
        origin = getattr(spec, "origin", "") or ""
    if not origin:
        origin = getattr(mod, "__file__", "") or ""
    return origin.lower().endswith((".pyd", ".so"))


def _verify_compiled_integrity() -> None:
    """分发包(frozen)中核心安全模块必须是原生扩展，否则核心功能 fail-closed。

    开发/源码运行（未 frozen）直接放行，不影响本地调试与单元测试。
    结果缓存：只在首次实际校验一次，避免每次动作重复反射。
    """
    global _integrity_ok
    if _integrity_ok is True:
        return
    if _integrity_ok is False:
        raise CapabilityError(
            "安全校验失败：核心模块未加密编译，疑似非官方构建，核心功能已停用。"
            "请使用官方发布版本。"
        )
    # 源码运行（开发/测试）不 frozen，放行。
    if not getattr(sys, "frozen", False):
        _integrity_ok = True
        return
    import importlib
    for name in _CORE_SECURITY_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:
            _integrity_ok = False
            raise CapabilityError(
                "安全校验失败：核心模块缺失，疑似非官方构建，核心功能已停用。"
            )
        if not _module_is_native(mod):
            _integrity_ok = False
            raise CapabilityError(
                "安全校验失败：核心模块未加密编译，疑似非官方构建，核心功能已停用。"
                "请使用官方发布版本。"
            )
    _integrity_ok = True


def verify_integrity_or_raise() -> None:
    """供启动期调用：分发包若为明文构建，立即暴露问题（不必等到首个动作）。"""
    _verify_compiled_integrity()


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
    """执行受控动作前调用；非官方构建或无有效服务端签名令牌则抛 CapabilityError。"""
    _verify_compiled_integrity()
    res = _get_validator().acquire_capability(action)
    if not res.get("ok"):
        raise CapabilityError(res.get("reason", "未获授权"))
