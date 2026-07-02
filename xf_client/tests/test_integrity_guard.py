"""分发包完整性 fail-closed 校验测试。

背景：Windows 曾误用普通打包发出明文源码包（验签公钥可被一行替换 → 方案B被绕过）。
本测试锁定（覆盖 PyInstaller 与 Nuitka 两种打包器）：
  - 源码运行（非分发构建）：完整性校验放行，不影响开发/测试。
  - PyInstaller(frozen) + 核心模块明文 .py：核心动作 fail-closed 抛 CapabilityError。
  - PyInstaller(frozen) + 核心模块原生扩展 .pyd/.so：放行。
  - Nuitka(__compiled__) + 核心模块为编译模块：放行。
  - Nuitka(__compiled__) + 核心模块明文 .py：必须 fail-closed（关键回归：
    旧逻辑只看 sys.frozen，在 Nuitka 包里会退化成"永远放行"，守卫形同虚设）。
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license.capability_guard as cg
from license.capability_guard import CapabilityError


class _FakeSpec:
    def __init__(self, origin):
        self.origin = origin


def _fake_module(origin, compiled=False):
    m = types.ModuleType("fake")
    m.__spec__ = _FakeSpec(origin)
    m.__file__ = origin
    if compiled:
        m.__compiled__ = "nuitka"
    return m


class IntegrityGuardTest(unittest.TestCase):
    def setUp(self):
        cg._integrity_ok = None
        self._frozen = getattr(sys, "frozen", None)
        self._had_compiled = "__compiled__" in cg.__dict__
        self._compiled_val = cg.__dict__.get("__compiled__")

    def tearDown(self):
        cg._integrity_ok = None
        if self._frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._frozen
        # 还原 Nuitka 标记
        if self._had_compiled:
            cg.__dict__["__compiled__"] = self._compiled_val
        else:
            cg.__dict__.pop("__compiled__", None)

    def _clear_frozen(self):
        if hasattr(sys, "frozen"):
            del sys.frozen

    def _patch_import(self, factory):
        import importlib
        orig = importlib.import_module
        importlib.import_module = factory
        return importlib, orig

    def test_source_run_passes(self):
        # 非分发构建（未 frozen、无 __compiled__）应直接放行。
        self._clear_frozen()
        cg.__dict__.pop("__compiled__", None)
        cg.verify_integrity_or_raise()  # 不抛异常即通过

    def test_frozen_plaintext_fails_closed(self):
        # PyInstaller 分发包中核心模块是明文 .py → 必须拒用。
        sys.frozen = True
        importlib, orig = self._patch_import(
            lambda name: _fake_module(f"/app/{name.replace('.', '/')}.py")
        )
        try:
            with self.assertRaises(CapabilityError):
                cg.verify_integrity_or_raise()
        finally:
            importlib.import_module = orig

    def test_frozen_native_passes(self):
        # PyInstaller 分发包中核心模块是原生扩展 .pyd → 放行。
        sys.frozen = True
        importlib, orig = self._patch_import(
            lambda name: _fake_module(f"/app/{name.replace('.', '/')}.pyd")
        )
        try:
            cg.verify_integrity_or_raise()  # 不抛异常即通过
        finally:
            importlib.import_module = orig

    def test_nuitka_compiled_passes(self):
        # Nuitka 分发包：守卫模块与核心模块均含 __compiled__ → 放行。
        self._clear_frozen()
        cg.__dict__["__compiled__"] = "nuitka"
        importlib, orig = self._patch_import(
            lambda name: _fake_module(f"/app/{name.replace('.', '/')}", compiled=True)
        )
        try:
            cg.verify_integrity_or_raise()  # 不抛异常即通过
        finally:
            importlib.import_module = orig

    def test_nuitka_plaintext_fails_closed(self):
        # 关键回归：Nuitka 构建（守卫模块含 __compiled__）但核心模块是明文 .py，
        # 必须 fail-closed；旧逻辑只看 sys.frozen 会退化成"永远放行"。
        self._clear_frozen()
        cg.__dict__["__compiled__"] = "nuitka"
        importlib, orig = self._patch_import(
            lambda name: _fake_module(f"/app/{name.replace('.', '/')}.py")
        )
        try:
            with self.assertRaises(CapabilityError):
                cg.verify_integrity_or_raise()
        finally:
            importlib.import_module = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
