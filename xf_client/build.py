"""一键测试脚本 + 加密打包重定向（跨平台）。

⚠️ 安全变更（重要）：
    旧版本 build.py 会用 `闲鱼AI助手.spec` 直接 PyInstaller 打包，把
    engine/license/config 当作**明文源码**塞进产物。这类明文包可被一行
    替换验签公钥而彻底破解（方案B防护形同虚设，已在 Windows 端出过事故）。

    因此本脚本不再产出明文分发包。打包一律走加密流程 `secure_build.py`
    （核心模块 Cython 编译为 .pyd/.so 原生扩展后再打包）。

用法：
    python build.py            # 测试 + 加密打包（等价 secure_build.py）
    python build.py --test     # 只跑测试
    python build.py --no-test  # 跳过测试直接加密打包（不推荐）
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECURE_BUILD = os.path.join(HERE, "secure_build.py")


def run_tests() -> bool:
    print("=" * 60)
    print("[1/1] 运行单元测试 …")
    print("=" * 60)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=HERE,
    )
    if proc.returncode != 0:
        print("\n❌ 测试未通过。")
        return False
    print("\n✅ 测试全部通过。")
    return True


def run_secure_build(extra_args: list[str]) -> int:
    print("=" * 60)
    print("→ 重定向到加密打包 secure_build.py（防明文源码泄露/破解）")
    print("=" * 60)
    return subprocess.run(
        [sys.executable, SECURE_BUILD, *extra_args], cwd=HERE
    ).returncode


def main() -> int:
    args = set(sys.argv[1:])
    only_test = "--test" in args
    skip_test = "--no-test" in args

    if only_test:
        return 0 if run_tests() else 1

    # 打包统一交给 secure_build.py（它内部也会跑测试；--no-test 透传）。
    extra = ["--no-test"] if skip_test else []
    return run_secure_build(extra)


if __name__ == "__main__":
    sys.exit(main())
