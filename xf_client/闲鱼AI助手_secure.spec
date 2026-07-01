# -*- mode: python ; coding: utf-8 -*-
# 安全打包 spec：核心模块已被 Cython 编译为原生扩展（.so/.pyd），
# 不再随包附带可读的 .py 源码。由 secure_build.py 在 STAGE 目录中调用。
from PyInstaller.utils.hooks import collect_all
import certifi
import os as _os
import sys as _sys

_BASE = _os.path.dirname(_os.path.abspath(SPEC)) if 'SPEC' in dir() else _os.getcwd()

# 版本号：优先环境变量（secure_build 注入），否则回退解析 config.py（若存在）。
APP_VERSION = _os.environ.get('XF_APP_VERSION', '')
if not APP_VERSION:
    import re as _re
    _cfgp = _os.path.join(_BASE, 'config.py')
    if _os.path.exists(_cfgp):
        _m = _re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)', open(_cfgp, encoding='utf-8').read())
        APP_VERSION = _m.group(1) if _m else '0.0.0'
    else:
        APP_VERSION = '0.0.0'

ICNS_PATH = _os.path.join(_BASE, 'assets', 'AppIcon.icns')
if not _os.path.exists(ICNS_PATH):
    ICNS_PATH = 'assets/AppIcon.icns'
ICO_PATH = _os.path.join(_BASE, 'assets', 'AppIcon.ico')
if not _os.path.exists(ICO_PATH):
    ICO_PATH = 'assets/AppIcon.ico'
EXE_ICON = ICO_PATH if _sys.platform.startswith('win') else ICNS_PATH

# 已编译为原生扩展的核心包：收集其模块名作为 hiddenimports，
# 因为 PyInstaller 的依赖分析无法扫描 .so/.pyd 内部的 import。
_CORE_DIRS = ['engine', 'license', 'utils', 'database']
_core_hidden = ['config']
for _d in _CORE_DIRS:
    _p = _os.path.join(_BASE, _d)
    if not _os.path.isdir(_p):
        continue
    for _root, _, _files in _os.walk(_p):
        for _f in _files:
            base = None
            if _f.endswith('.py') and _f != '__init__.py':
                base = _f[:-3]
            elif _f.endswith('.so'):
                base = _f.split('.')[0]
            elif _f.endswith('.pyd'):
                base = _f.split('.')[0]
            if not base or base == '__init__':
                continue
            rel = _os.path.relpath(_os.path.join(_root, base), _BASE)
            _core_hidden.append(rel.replace(_os.sep, '.'))
_core_hidden = sorted(set(_core_hidden))

datas_drission, binaries_drission, hiddenimports_drission = collect_all('DrissionPage')
datas_aiohttp, binaries_aiohttp, hiddenimports_aiohttp = collect_all('aiohttp')
datas_openpyxl, binaries_openpyxl, hiddenimports_openpyxl = collect_all('openpyxl')

cert_file = certifi.where()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        *binaries_drission,
        *binaries_aiohttp,
        *binaries_openpyxl,
    ],
    datas=[
        # 注意：不再附带 engine/license/utils/database/config.py 源码，
        # 这些已编译为原生扩展并通过 hiddenimports 收集。
        ('ui', 'ui'),
        ('assets', 'assets'),
        (cert_file, 'certifi'),
        *datas_drission,
        *datas_aiohttp,
        *datas_openpyxl,
    ],
    hiddenimports=[
        'PyQt6', 'PyQt6.QtCore', 'PyQt6.QtWidgets', 'PyQt6.QtGui',
        'DrissionPage', 'DrissionPage.chromium', 'DrissionPage._units',
        'DrissionPage._configs', 'DrissionPage._pages',
        'DrissionPage._elements', 'DrissionPage._actions',
        'aiohttp', 'openpyxl', 'requests', 'certifi',
        'cryptography', 'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.primitives.asymmetric.padding',
        'cryptography.hazmat.primitives.hashes',
        'PIL', 'PIL.Image',
        'hashlib', 'json', 'platform', 'subprocess',
        *_core_hidden,
        *hiddenimports_drission,
        *hiddenimports_aiohttp,
        *hiddenimports_openpyxl,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ── 按平台分支 ──────────────────────────────────────────────
# Windows：onefile 单文件 exe（binaries/datas 全部打进 exe），
#          客户双击即用，无需附带 _internal 文件夹。
# macOS：  onedir + .app bundle（Qt 框架含大量符号链接，
#          单文件模式会破坏 .app 结构，故 mac 仍用目录模式）。
if _sys.platform.startswith('win'):
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='闲鱼AI助手',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=EXE_ICON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='闲鱼AI助手',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=EXE_ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='闲鱼AI助手',
    )
    app = BUNDLE(
        coll,
        name='闲鱼AI助手.app',
        icon=ICNS_PATH,
        bundle_identifier='com.xianyu.aihelper',
        version=APP_VERSION,
        info_plist={
            'CFBundleShortVersionString': APP_VERSION,
            'CFBundleVersion': APP_VERSION,
            'CFBundleDisplayName': '闲鱼AI助手',
            'NSHumanReadableCopyright': '闲鱼AI助手 v' + APP_VERSION,
            'NSHighResolutionCapable': True,
        },
    )
