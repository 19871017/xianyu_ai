# -*- mode: python ; coding: utf-8 -*-

# 收集DrissionPage全部文件
from PyInstaller.utils.hooks import collect_all
import certifi
import re as _re
_cfg=open('config.py',encoding='utf-8').read()
_m=_re.search(r'APP_VERSION\s*=\s*[\"\']([^\"\']+)', _cfg)
APP_VERSION=_m.group(1) if _m else '0.0.0'
import os as _os
import sys as _sys
_BASE=_os.path.dirname(_os.path.abspath(SPEC)) if 'SPEC' in dir() else _os.getcwd()
ICNS_PATH=_os.path.join(_BASE,'assets','AppIcon.icns')
if not _os.path.exists(ICNS_PATH):
    ICNS_PATH='assets/AppIcon.icns'
ICO_PATH=_os.path.join(_BASE,'assets','AppIcon.ico')
if not _os.path.exists(ICO_PATH):
    ICO_PATH='assets/AppIcon.ico'
# EXE 图标：Windows 用 .ico，其余（mac/linux）用 .icns
EXE_ICON=ICO_PATH if _sys.platform.startswith('win') else ICNS_PATH

datas_drission, binaries_drission, hiddenimports_drission = collect_all('DrissionPage')
datas_aiohttp, binaries_aiohttp, hiddenimports_aiohttp = collect_all('aiohttp')
datas_openpyxl, binaries_openpyxl, hiddenimports_openpyxl = collect_all('openpyxl')

# SSL证书路径
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
        ('config.py', '.'),
        ('engine', 'engine'),
        ('license', 'license'),
        ('ui', 'ui'),
        ('utils', 'utils'),
        ('database', 'database'),
        ('assets', 'assets'),
        (cert_file, 'certifi'),
        *datas_drission,
        *datas_aiohttp,
        *datas_openpyxl,
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtWidgets',
        'PyQt6.QtGui',
        'DrissionPage',
        'DrissionPage.chromium',
        'DrissionPage._units',
        'DrissionPage._configs',
        'DrissionPage._pages',
        'DrissionPage._elements',
        'DrissionPage._actions',
        'aiohttp',
        'openpyxl',
        'requests',
        'hashlib',
        'json',
        'platform',
        'subprocess',
        'database',
        'database.db_manager',
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='闲鱼AI助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
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
