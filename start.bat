@echo off
chcp 65001 >nul 2>&1
title SoulHealth BioCompute - 一键启动

echo.
echo ==================================================
echo   SoulHealth 生物计算平台 - 一键启动
echo ==================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 goto NO_PYTHON
echo [OK] Python 已就绪
goto DO_INSTALL

:NO_PYTHON
echo [错误] 未检测到 Python，请先安装 Python 3.10+
echo        下载地址: https://www.python.org/downloads/
echo        安装时务必勾选 Add Python to PATH
pause
exit /b 1

:DO_INSTALL
:: 安装依赖
echo [*] 正在检查/安装依赖...
pip install -r "%~dp0requirements.txt" -q 2>nul
echo [OK] 依赖已就绪

:: 检查 .env
if not exist "%~dp0.env" (
    echo [!] 未找到 .env 配置文件
    if exist "%~dp0.env.example" (
        echo [*] 自动从 .env.example 创建...
        copy "%~dp0.env.example" "%~dp0.env" >nul
        echo [OK] 已创建 .env，请根据需要编辑配置
    ) else (
        echo [!] 也没找到 .env.example，将以默认配置启动
    )
)

:: 启动
echo.
echo [*] 启动服务...
echo.
echo ==================================================
echo   访问地址: http://127.0.0.1:9000
echo   默认账号: admin / admin123
echo   按 Ctrl+C 停止服务
echo ==================================================
echo.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set SOULHEALTH_PORT=9000
python run_api.py
pause
