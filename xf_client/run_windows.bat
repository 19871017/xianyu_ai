@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo   闲鱼AI助手 Windows 启动脚本
echo   (无需打包，直接运行)
echo ========================================
echo.

REM 切换到脚本所在目录
cd /d "%~dp0"

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python！
    echo.
    echo 请先安装 Python 3.10+:
    echo   1. 访问 https://www.python.org/downloads/
    echo   2. 下载并安装
    echo   3. 安装时务必勾选 "Add Python to PATH"
    echo   4. 安装完成后重新运行此脚本
    echo.
    pause
    exit /b 1
)

echo [1/3] Python: 
python --version

REM 检查pip依赖是否已安装
echo [2/3] 检查依赖...
python -c "import PyQt6" 2>nul
if errorlevel 1 (
    echo   首次运行，正在安装依赖...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败！
        echo 请尝试: pip install --user -r requirements.txt
        pause
        exit /b 1
    )
    echo   依赖安装完成！
) else (
    echo   依赖已就绪
)

REM 启动程序
echo [3/3] 启动闲鱼AI助手...
echo.
python main.py

if errorlevel 1 (
    echo.
    echo [程序异常退出] 错误码: %errorlevel%
    echo.
    echo 常见问题:
    echo   1. 缺少Chrome浏览器 → 安装Google Chrome
    echo   2. DrissionPage版本问题 → pip install DrissionPage==4.0.0
    echo   3. PyQt6问题 → pip install --upgrade PyQt6
    echo.
    pause
)

endlocal
