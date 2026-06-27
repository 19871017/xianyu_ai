"""一键测试 + 打包脚本（跨平台）。

流程：
  1. 运行全套单元测试（tests/），任一失败则中止，不进行打包。
  2. 测试通过后调用 PyInstaller 按 闲鱼AI助手.spec 打包。
  3. 打包产物：
       - macOS: dist/闲鱼AI助手.app + dist/闲鱼AI助手/
       - Windows: dist/闲鱼AI助手/闲鱼AI助手.exe

用法：
    python build.py            # 测试 + 打包
    python build.py --test     # 只测试
    python build.py --no-test  # 跳过测试直接打包（不推荐）
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "闲鱼AI助手.spec")


def run_tests() -> bool:
    print("=" * 60)
    print("[1/2] 运行单元测试 …")
    print("=" * 60)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=HERE,
    )
    if proc.returncode != 0:
        print("\n❌ 测试未通过，已中止打包。")
        return False
    print("\n✅ 测试全部通过。")
    return True


def run_build() -> bool:
    print("=" * 60)
    print("[2/2] PyInstaller 打包 …")
    print("=" * 60)
    if not os.path.exists(SPEC):
        print(f"❌ 找不到 spec 文件: {SPEC}")
        return False
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", SPEC],
        cwd=HERE,
    )
    if proc.returncode != 0:
        print("\n❌ 打包失败。")
        return False
    out = os.path.join(HERE, "dist")
    print(f"\n✅ 打包完成，产物目录: {out}")
    for name in os.listdir(out) if os.path.isdir(out) else []:
        print(f"   - {name}")
    return True


def main() -> int:
    args = set(sys.argv[1:])
    only_test = "--test" in args
    skip_test = "--no-test" in args

    if not skip_test:
        if not run_tests():
            return 1
    if only_test:
        return 0
    return 0 if run_build() else 1


if __name__ == "__main__":
    sys.exit(main())
