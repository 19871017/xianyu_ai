@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   闲鱼AI助手 Windows 打包脚本 v2
echo ========================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

REM 切换到脚本所在目录（确保main.py在当前目录）
cd /d "%~dp0"

echo [1/6] 当前目录: %CD%
echo [1/6] main.py存在检查:
if not exist "main.py" (
    echo [错误] 找不到main.py！请确保bat和main.py在同一目录
    pause
    exit /b 1
)
echo       ✓ main.py OK

REM 安装依赖
echo.
echo [2/6] 安装Python依赖...
pip install --upgrade pip -q
pip install PyQt6 DrissionPage aiohttp openpyxl requests certifi pyinstaller -q

REM 清理旧打包
if exist "dist" rmdir /s /q dist
if exist "build" rmdir /s /q build

REM ============================================
REM 方式A: PyInstaller — 最可靠方案
REM ============================================
echo.
echo [3/6] 使用 PyInstaller 打包...
echo       这可能需要2-5分钟，请耐心等待...

pyinstaller --noconfirm --onedir --windowed ^
    --name "闲鱼AI助手" ^
    --hidden-import=PyQt6 ^
    --hidden-import=PyQt6.QtCore ^
    --hidden-import=PyQt6.QtWidgets ^
    --hidden-import=PyQt6.QtGui ^
    --hidden-import=DrissionPage ^
    --collect-all DrissionPage ^
    --collect-all aiohttp ^
    --collect-all openpyxl ^
    main.py

if exist "dist\闲鱼AI助手\闲鱼AI助手.exe" (
    goto :success_pyinstaller
)

echo.
echo [警告] PyInstaller onedir模式也失败，尝试onefile...
rmdir /s /q dist 2>nul
rmdir /s /q build 2>nul

pyinstaller --noconfirm --onefile --windowed ^
    --name "闲鱼AI助手" ^
    --hidden-import=PyQt6 ^
    --hidden-import=PyQt6.QtCore ^
    --hidden-import=PyQt6.QtWidgets ^
    --hidden-import=PyQt6.QtGui ^
    --hidden-import=DrissionPage ^
    --collect-all DrissionPage ^
    --collect-all aiohttp ^
    --collect-all openpyxl ^
    main.py

if exist "dist\闲鱼AI助手.exe" (
    mkdir output 2>nul
    copy "dist\闲鱼AI助手.exe" "output\闲鱼AI助手.exe"
    goto :done
)

echo.
echo [错误] PyInstaller两种模式都失败了！
echo.
echo 可能的原因:
echo   1. DrissionPage与当前Python版本不兼容
echo   2. 缺少Visual C++ Redistributable
echo   3. pip安装的包有问题
echo.
echo 建议尝试:
echo   1. python -m pip uninstall DrissionPage -y && pip install DrissionPage==4.0.0
echo   2. 安装 Visual C++ Redistributable 2015-2022 (x64)
echo   3. 使用虚拟环境: python -m venv venv && venv\Scripts\activate.bat
echo.
pause
exit /b 1

:success_pyinstaller
echo.
echo [4/6] 整理输出...
mkdir output 2>nul
xcopy /E /I /Y "dist\闲鱼AI助手" "output\闲鱼AI助手" >nul

REM 写使用说明
echo [5/6] 生成说明文件...
(
echo 闲鱼AI助手 使用说明
echo ====================
echo.
echo 1. 进入 output\闲鱼AI助手 目录
echo 2. 双击 闲鱼AI助手.exe 运行
echo 3. 首次运行会较慢，请耐心等待
echo 4. 请先在"设置"页面激活License
echo 5. 在"设置"页面配置AI API（兼容OpenAI格式中转）
echo 6. 采集功能需要Chrome浏览器已安装
echo 7. 如被杀毒软件误报，请添加白名单
echo.
echo 支持的AI API:
echo   - DeepSeek: https://api.deepseek.com
echo   - OpenAI: https://api.openai.com/v1
echo   - OneAPI/NewAPI: http://你的地址:3000
echo   - 任意OpenAI兼容中转站
) > output\使用说明.txt

:done
echo.
echo [6/6] 完成！
echo ========================================
echo   打包成功！
echo   输出: output\
echo ========================================
dir output\
echo.
pause
