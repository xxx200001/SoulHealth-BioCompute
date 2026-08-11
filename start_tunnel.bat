@echo off
chcp 65001 >nul 2>&1
title SoulHealth BioCompute - 一键公网穿透

echo.
echo ==================================================
echo   SoulHealth 生物计算平台 - 开启公网临时穿透
echo ==================================================
echo.
echo [*] 正在开启公网访问 (端口 9000)...
echo [*] 稍后终端中输出的 https://xxxx.trycloudflare.com 即为公网链接
echo.

where cloudflared >nul 2>&1
if %errorlevel% equ 0 (
    cloudflared tunnel --url http://localhost:9000
) else (
    npx cloudflared tunnel --url http://localhost:9000
)
pause
