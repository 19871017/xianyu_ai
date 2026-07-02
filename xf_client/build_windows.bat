@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   闲鱼采集上架助手 Windows 加密打包脚本 v4
echo   （核心模块 Cython 编译为 .pyd 原生扩展后再打包）
echo ========================================
echo.

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist "main.py" (
    echo [错误] 找不到 main.py，请确保 bat 与 main.py 在同一目录
    pause
    exit /b 1
)
echo [1/5] main.py 检查 OK

REM 检查 C 编译器（Cython 编译 .pyd 需要 MSVC Build Tools）
echo.
echo [2/5] 检查 C 编译器（Cython 需要）...
where cl >nul 2>&1
if errorlevel 1 (
    echo [警告] 未检测到 MSVC 编译器 cl.exe。
    echo        Cython 加密编译需要 "Microsoft C++ Build Tools"。
    echo        下载: https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo        安装时勾选 "使用 C++ 的桌面开发"，然后在
    echo        "x64 Native Tools Command Prompt for VS" 中重新运行本脚本。
    echo.
    echo        是否仍要继续尝试（编译失败会中止）？按任意键继续，或关闭窗口取消。
    pause >nul
) else (
    echo       已检测到 MSVC 编译器
)

REM 安装依赖（pillow 生成 .ico 图标；Cython 用于加密编译核心模块）
echo.
echo [3/5] 安装 Python 依赖...
python -m ensurepip --upgrade >nul 2>&1
python -m pip install --upgrade pip
python -m pip install PyQt6 DrissionPage aiohttp openpyxl requests certifi cryptography pillow pyinstaller Cython setuptools
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)
REM 校验关键依赖确实可导入（uv 等托管的 Python 有时会把包装到别处，pip 成功但导入失败）
python -c "import requests, PyQt6, DrissionPage, openpyxl, cryptography, certifi, Cython" 2>nul
if errorlevel 1 (
    echo [错误] 关键依赖导入失败：当前 python 可能由 uv 等工具托管，pip 装到了别处。
    echo        当前解释器：
    where python
    python --version
    echo        建议安装官方 Python 3.11 并勾选 "Add python.exe to PATH"：
    echo        https://www.python.org/downloads/
    echo        然后在 "x64 Native Tools Command Prompt for VS" 中重新运行本脚本。
    pause
    exit /b 1
)
echo       依赖校验通过

REM 生成 Windows 图标（若缺失）
echo.
echo [4/5] 准备应用图标...
if not exist "assets\AppIcon.ico" (
    echo       生成 assets\AppIcon.ico ...
    python tools_make_icon.py
) else (
    echo       assets\AppIcon.ico 已存在
)

REM 清理旧打包
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build
if exist "build_secure_stage" rmdir /s /q build_secure_stage

REM 走加密 spec 打包（先跑测试 -> Cython 编译核心模块 -> PyInstaller）
echo.
echo [5/5] 测试 + Cython 加密编译 + PyInstaller 打包...
echo       这可能需要 3-8 分钟，请耐心等待...
python secure_build.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败。常见排查：
    echo   1. 安装 "Microsoft C++ Build Tools"（Cython 编译 .pyd 必需）
    echo   2. 在 "x64 Native Tools Command Prompt for VS" 中运行本脚本
    echo   3. 安装 Visual C++ Redistributable 2015-2022 x64
    echo   4. python -m pip install --force-reinstall DrissionPage
    pause
    exit /b 1
)

REM 整理输出（onefile 单文件模式：产物为 dist\闲鱼AI助手.exe）
echo.
echo 整理输出...
if exist "dist\闲鱼AI助手.exe" (
    if exist "output" rmdir /s /q output
    mkdir output
    copy /Y "dist\闲鱼AI助手.exe" "output\闲鱼AI助手.exe" >nul
    (
    echo 闲鱼采集上架助手 使用说明
    echo ========================
    echo.
    echo 1. 双击 闲鱼AI助手.exe 直接运行（单文件，无需解压附带文件夹）
    echo 2. 首次运行会较慢（自解压），请耐心等待
    echo 3. 请先在"设置"页面激活 License
    echo 4. 在"设置"页面配置 AI API（兼容 OpenAI 格式中转）
    echo 5. 采集功能需要已安装 Chrome 浏览器
    echo 6. 如被杀毒软件误报，请添加白名单
    echo.
    echo 注：本版本核心模块已编译为原生扩展（.pyd），不含可读源码。
    ) > "output\使用说明.txt"
    echo.
    echo ========================================
    echo   打包成功（加密·单文件）！输出: output\闲鱼AI助手.exe
    echo ========================================
    dir output\
) else (
    echo [错误] 未找到打包产物 dist\闲鱼AI助手.exe
    pause
    exit /b 1
)
echo.
pause
