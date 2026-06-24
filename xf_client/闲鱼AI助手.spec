# -*- mode: python ; coding: utf-8 -*-

# 收集DrissionPage全部文件
from PyInstaller.utils.hooks import collect_all
import certifi

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
    icon=None,
    bundle_identifier=None,
)
