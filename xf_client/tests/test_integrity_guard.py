"""分发包完整性 fail-closed 校验测试。

背景：Windows 曾误用普通打包发出明文源码包（验签公钥可被一行替换 → 方案B被绕过）。
本测试锁定：
  - 源码运行（未 frozen）：完整性校验放行，不影响开发/测试。
  - 模拟 frozen + 核心模块为明文：核心动作 fail-closed 抛 CapabilityError。
  - 模拟 frozen + 核心模块为原生扩展(.pyd/.so)：放行。
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


def _fake_module(origin):
    m = types.ModuleType("fake")
    m.__spec__ = _FakeSpec(origin)
    m.__file__ = origin
    return m


class IntegrityGuardTest(unittest.TestCase):
    def setUp(self):
        cg._integrity_ok = None
        self._frozen = getattr(sys, "frozen", None)

    def tearDown(self):
        cg._integrity_ok = None
        if self._frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = self._frozen

    def test_source_run_passes(self):
        # 未 frozen（开发/测试）应直接放行。
        if hasattr(sys, "frozen"):
            del sys.frozen
        cg.verify_integrity_or_raise()  # 不抛异常即通过

    def test_frozen_plaintext_fails_closed(self):
        # 模拟分发包中核心模块是明文 .py → 必须拒用。
        sys.frozen = True
        import importlib
        orig = importlib.import_module

        def fake_import(name):
            return _fake_module(f"/app/{name.replace('.', '/')}.py")

        importlib.import_module = fake_import
        try:
            with self.assertRaises(CapabilityError):
                cg.verify_integrity_or_raise()
        finally:
            importlib.import_module = orig

    def test_frozen_native_passes(self):
        # 模拟分发包中核心模块是原生扩展 .pyd → 放行。
        sys.frozen = True
        import importlib
        orig = importlib.import_module

        def fake_import(name):
            return _fake_module(f"/app/{name.replace('.', '/')}.pyd")

        importlib.import_module = fake_import
        try:
            cg.verify_integrity_or_raise()  # 不抛异常即通过
        finally:
            importlib.import_module = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
