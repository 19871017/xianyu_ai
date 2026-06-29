"""安全打包脚本：核心模块 Cython 编译为原生扩展后再 PyInstaller 打包。

防逆向策略（与明文打包的区别）：
  - engine / license / utils / database / config.py 经 Cython 编译为
    平台原生扩展（macOS: .so，Windows: .pyd），几乎无法反编译还原源码。
  - 打包产物不再附带这些目录的可读 .py 源码（旧 spec 会把整个 engine/
    license 作为 datas 明文塞进包里，相当于直接发源码）。
  - UI 层（ui/）仍以字节码形式进入 PyInstaller 归档（optimize=2，去 docstring）。

跨平台说明：
  - Cython 产物与平台 / Python 版本绑定，无法交叉编译。
  - 在 macOS 上运行本脚本 → 产出加密的 mac 包。
  - 在 Windows 上运行（build_windows.bat 调用）→ 产出加密的 win 包。

用法：
    python secure_build.py            # 测试 + 加密编译 + 打包
    python secure_build.py --no-test  # 跳过测试（不推荐）
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig

HERE = os.path.dirname(os.path.abspath(__file__))
SECURE_SPEC = os.path.join(HERE, "闲鱼AI助手_secure.spec")

# 需要编译为原生扩展的核心包/模块（值钱、需防逆向的部分）。
CORE_PACKAGES = ["engine", "license", "utils", "database"]
CORE_SINGLE_FILES = ["config.py"]
# 这些模块即使在核心包内，也保持 .py（被 Cython 编译会破坏运行期 __file__/资源逻辑，或属入口）。
KEEP_SOURCE = set()


def _app_version() -> str:
    cfg = open(os.path.join(HERE, "config.py"), encoding="utf-8").read()
    m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)', cfg)
    return m.group(1) if m else "0.0.0"


def run_tests() -> bool:
    print("=" * 60)
    print("[1/4] 运行单元测试 ...")
    print("=" * 60)
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=HERE,
    )
    if proc.returncode != 0:
        print("\n[FAIL] 测试未通过，已中止打包。")
        return False
    print("\n[OK] 测试全部通过。")
    return True


def _stage(stage_dir: str) -> None:
    """复制项目到 stage_dir（排除产物/缓存/虚拟环境/git）。"""
    if os.path.exists(stage_dir):
        shutil.rmtree(stage_dir)
    ignore = shutil.ignore_patterns(
        "build", "dist", "__pycache__", "*.pyc", ".git", ".venv*",
        "*.log", ".DS_Store", "tests",
    )
    shutil.copytree(HERE, stage_dir, ignore=ignore)


def _cythonize(stage_dir: str) -> bool:
    """在 stage_dir 内把核心模块编译为原生扩展，并删除对应 .py 源码。"""
    print("=" * 60)
    print("[2/4] Cython 编译核心模块为原生扩展 ...")
    print("=" * 60)
    try:
        from Cython.Build import cythonize  # noqa: F401
    except Exception as e:
        print(f"[FAIL] 未安装 Cython: {e}\n  pip install Cython")
        return False

    # 收集待编译的 .py（绝对路径），同时记录用于编译后删除。
    targets: list[str] = []
    for pkg in CORE_PACKAGES:
        p = os.path.join(stage_dir, pkg)
        if not os.path.isdir(p):
            continue
        for root, _, files in os.walk(p):
            for f in files:
                if not f.endswith(".py") or f == "__init__.py":
                    continue
                rel = os.path.relpath(os.path.join(root, f), stage_dir)
                if rel in KEEP_SOURCE:
                    continue
                targets.append(os.path.join(root, f))
    for f in CORE_SINGLE_FILES:
        fp = os.path.join(stage_dir, f)
        if os.path.exists(fp):
            targets.append(fp)

    if not targets:
        print("[FAIL] 没有找到待编译模块。")
        return False

    # 用 setup.py + build_ext --inplace 编译（就地生成 .so/.pyd 到模块旁）。
    setup_py = os.path.join(stage_dir, "_secure_setup.py")
    rel_targets = [os.path.relpath(t, stage_dir).replace(os.sep, "/") for t in targets]
    setup_src = (
        "from setuptools import setup\n"
        "from Cython.Build import cythonize\n"
        "import sys\n"
        "TARGETS = %r\n"
        "setup(\n"
        "    script_args=['build_ext', '--inplace'],\n"
        "    ext_modules=cythonize(\n"
        "        TARGETS,\n"
        "        language_level=3,\n"
        "        quiet=True,\n"
        "    ),\n"
        ")\n"
    ) % rel_targets
    open(setup_py, "w", encoding="utf-8").write(setup_src)

    proc = subprocess.run([sys.executable, "_secure_setup.py"], cwd=stage_dir)
    if proc.returncode != 0:
        print("[FAIL] Cython 编译失败。")
        return False

    # 校验每个目标都生成了扩展，再删除 .py 与中间 .c。
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    missing = []
    for t in targets:
        mod_dir = os.path.dirname(t)
        base = os.path.basename(t)[:-3]
        produced = [
            n for n in os.listdir(mod_dir)
            if (n.startswith(base + ".") and (n.endswith(".so") or n.endswith(".pyd")))
        ]
        if not produced:
            missing.append(os.path.relpath(t, stage_dir))
            continue
        os.remove(t)
        c_file = t[:-3] + ".c"
        if os.path.exists(c_file):
            os.remove(c_file)

    if missing:
        print("[FAIL] 以下模块未生成原生扩展：")
        for m in missing:
            print("   -", m)
        return False

    # 清理编译中间产物
    for d in ("build",):
        bp = os.path.join(stage_dir, d)
        if os.path.exists(bp):
            shutil.rmtree(bp)
    if os.path.exists(setup_py):
        os.remove(setup_py)

    # 二次确认：核心目录内不应再有可读 .py（__init__.py 除外）
    leftover = []
    for pkg in CORE_PACKAGES:
        p = os.path.join(stage_dir, pkg)
        for root, _, files in os.walk(p):
            for f in files:
                if f.endswith(".py") and f != "__init__.py":
                    leftover.append(os.path.relpath(os.path.join(root, f), stage_dir))
    if os.path.exists(os.path.join(stage_dir, "config.py")):
        leftover.append("config.py")
    if leftover:
        print("[FAIL] 仍存在未编译的明文源码：", leftover)
        return False

    print(f"[OK] 已编译 {len(targets)} 个核心模块为原生扩展，源码已从打包目录移除。")
    return True


def run_build(stage_dir: str) -> bool:
    print("=" * 60)
    print("[3/4] PyInstaller 打包（加密产物）...")
    print("=" * 60)
    spec_in_stage = os.path.join(stage_dir, os.path.basename(SECURE_SPEC))
    shutil.copy2(SECURE_SPEC, spec_in_stage)
    env = dict(os.environ)
    env["XF_APP_VERSION"] = _app_version()
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", os.path.basename(SECURE_SPEC)],
        cwd=stage_dir, env=env,
    )
    if proc.returncode != 0:
        print("\n[FAIL] 打包失败。")
        return False
    return True


def main() -> int:
    args = set(sys.argv[1:])
    skip_test = "--no-test" in args

    if not skip_test and not run_tests():
        return 1

    stage_dir = os.path.join(HERE, "build_secure_stage")
    print(f"\n暂存目录: {stage_dir}")
    _stage(stage_dir)
    if not _cythonize(stage_dir):
        return 1
    if not run_build(stage_dir):
        return 1

    # 把产物搬回主 dist/ 方便后续上传脚本统一处理。
    src_dist = os.path.join(stage_dir, "dist")
    dst_dist = os.path.join(HERE, "dist")
    if os.path.exists(dst_dist):
        shutil.rmtree(dst_dist)
    # 保留符号链接（symlinks=True）：Qt 框架内含大量符号链接，
    # 默认跟随会展开成重复实体并破坏 .app 代码签名封印，导致启动段错误。
    shutil.copytree(src_dist, dst_dist, symlinks=True)

    print("=" * 60)
    print("[4/4] 完成。加密产物目录: " + dst_dist)
    print("=" * 60)
    for name in os.listdir(dst_dist):
        print("   -", name)
    print("\n提示：核心模块已编译为原生扩展，包内无可读业务源码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
