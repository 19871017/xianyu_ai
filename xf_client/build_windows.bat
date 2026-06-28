@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   闲鱼采集上架助手 Windows 打包脚本 v3
echo   （与 mac 共用 闲鱼AI助手.spec 单一配置）
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

REM 安装依赖（pillow 用于生成 Windows .ico 图标）
echo.
echo [2/5] 安装 Python 依赖...
python -m pip install --upgrade pip -q
python -m pip install PyQt6 DrissionPage aiohttp openpyxl requests certifi pillow pyinstaller -q

REM 生成 Windows 图标（若缺失）
echo.
echo [3/5] 准备应用图标...
if not exist "assets\AppIcon.ico" (
    echo       生成 assets\AppIcon.ico ...
    python tools_make_icon.py
) else (
    echo       assets\AppIcon.ico 已存在
)

REM 清理旧打包
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build

REM 走统一 spec 打包（先跑测试，测试失败则中止）
echo.
echo [4/5] 测试 + PyInstaller 打包（统一 spec）...
echo       这可能需要 2-5 分钟，请耐心等待...
python build.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败。常见排查：
    echo   1. 安装 Visual C++ Redistributable 2015-2022 (x64)
    echo   2. python -m pip install --force-reinstall DrissionPage
    echo   3. 使用虚拟环境: python -m venv venv ^&^& venv\Scripts\activate.bat
    pause
    exit /b 1
)

REM 整理输出
echo.
echo [5/5] 整理输出...
if exist "dist\闲鱼AI助手" (
    if exist "output" rmdir /s /q output
    mkdir output
    xcopy /E /I /Y "dist\闲鱼AI助手" "output\闲鱼AI助手" >nul
    (
    echo 闲鱼采集上架助手 使用说明
    echo ========================
    echo.
    echo 1. 进入 output\闲鱼AI助手 目录
    echo 2. 双击 闲鱼AI助手.exe 运行
    echo 3. 首次运行会较慢，请耐心等待
    echo 4. 请先在"设置"页面激活 License
    echo 5. 在"设置"页面配置 AI API（兼容 OpenAI 格式中转）
    echo 6. 采集功能需要已安装 Chrome 浏览器
    echo 7. 如被杀毒软件误报，请添加白名单
    ) > "output\使用说明.txt"
    echo.
    echo ========================================
    echo   打包成功！输出: output\闲鱼AI助手\
    echo ========================================
    dir output\
) else (
    echo [错误] 未找到打包产物 dist\闲鱼AI助手
    pause
    exit /b 1
)
echo.
pause
