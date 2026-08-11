@echo off
chcp 65001 >nul 2>&1
title SoulHealth BioCompute - 一键启动
color 0A

echo.
echo  ========================================
echo   SoulHealth 生物计算平台 - 一键启动
echo  ========================================
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo        下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python 已就绪

:: 安装依赖
echo [..] 正在检查/安装依赖...
pip install -r requirements.txt -q 2>nul
echo [OK] 依赖已就绪

:: 启动
echo [..] 启动服务...
echo.
echo  访问地址: http://127.0.0.1:9000
echo  按 Ctrl+C 停止服务
echo.
python run_api.py
pause
