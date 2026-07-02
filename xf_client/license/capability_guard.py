"""能力守卫（方案B核心）：受控动作执行前强制向服务端换取签名令牌。

设计要点：
- 本模块与 license_validator/signature 同属 license 包，分发时被编译为原生
  产物（PyInstaller+Cython 的 .so/.pyd，或 Nuitka 的整体二进制），破解者要
  绕过必须改二进制而非改 .py 源码。
- engine 层核心入口（采集/上架/AI改写）调用 ``require_capability(action)``，
  拿不到有效令牌即抛 CapabilityError，令破解版即便 UI 显示"已激活"也调不动核心功能。
- 持有进程级 LicenseValidator 单例，避免改动 engine 各类的构造签名。

fail-closed 完整性校验：
- 分发包中，核心安全模块必须是编译产物（Cython 原生扩展 或 Nuitka 编译模块）。
- 一旦被误打成明文源码包（源码可读、验签公钥可被一行替换），核心功能一律拒用。
  这道校验把"误用普通打包发出可破解的明文包"从静默事故变成当场失败，逼迫重打加密版。

跨打包器说明：
- PyInstaller：设置 ``sys.frozen``；核心模块以 .so/.pyd 落地，靠模块 origin 后缀判定。
- Nuitka：不设 ``sys.frozen``，改在每个编译模块注入 ``__compiled__`` 属性；
  整个程序编译进单一二进制，无 .so/.pyd 落地，靠 ``__compiled__`` 判定。
  若不识别 Nuitka，旧逻辑会在 Nuitka 包里退化成"永远放行"（守卫失效）——故必须区分。
"""
from __future__ import annotations

import sys
import threading

_lock = threading.Lock()
_validator = None

# 分发包中必须为编译产物的核心安全模块（明文任一即判非官方构建）。
_CORE_SECURITY_MODULES = (
    "license.signature",
    "license.license_validator",
    "license.capability_guard",
    "config",
)
_integrity_ok = None


class CapabilityError(RuntimeError):
    """能力令牌获取失败：未授权 / 未联网 / 令牌无效 / 非官方构建。"""


def _running_under_nuitka() -> bool:
    """本模块是否由 Nuitka 编译（编译后模块命名空间含 __compiled__）。"""
    return "__compiled__" in globals()


def _is_distribution_build() -> bool:
    """是否为分发构建：PyInstaller(frozen) 或 Nuitka(__compiled__)。"""
    return bool(getattr(sys, "frozen", False)) or _running_under_nuitka()


def _module_is_native(mod) -> bool:
    """模块是否为编译产物，而非纯 Python 源码/字节码。

    - Nuitka：编译模块的命名空间含 ``__compiled__`` 属性。
    - PyInstaller+Cython：模块 origin 以 .pyd/.so 结尾。
    """
    if getattr(mod, "__compiled__", None) is not None:
        return True
    origin = ""
    spec = getattr(mod, "__spec__", None)
    if spec is not None:
        origin = getattr(spec, "origin", "") or ""
    if not origin:
        origin = getattr(mod, "__file__", "") or ""
    return origin.lower().endswith((".pyd", ".so"))


def _verify_compiled_integrity() -> None:
    """分发包中核心安全模块必须是编译产物，否则核心功能 fail-closed。

    开发/源码运行（非分发构建）直接放行，不影响本地调试与单元测试。
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
    # 源码运行（开发/测试）非分发构建，放行。
    if not _is_distribution_build():
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
