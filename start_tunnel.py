# -*- coding: utf-8 -*-
"""一键 Cloudflare 隧道启动脚本

用法: python start_tunnel.py [端口号]
默认端口: 9000 (BioCompute 后端)

会依次尝试:
  1. 系统 PATH 中的 cloudflared
  2. 当前目录下的 cloudflared.exe
  3. winget 安装路径
  4. npx 临时下载
"""
import os
import re
import sys
import glob
import shutil
import subprocess

port = sys.argv[1] if len(sys.argv) > 1 else "9000"

print("=" * 58, flush=True)
print(f"  SoulHealth 平台 - 开启公网穿透 (端口 {port})", flush=True)
print("=" * 58 + "\n", flush=True)


def find_cloudflared():
    """查找 cloudflared 可执行文件"""
    # 1. PATH 中
    cf = shutil.which("cloudflared")
    if cf:
        return cf
    # 2. 当前目录
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    if os.path.isfile(local):
        return local
    # 3. winget 安装路径
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        pattern = os.path.join(appdata, "Microsoft", "WinGet", "Packages",
                               "*cloudflared*", "cloudflared.exe")
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def launch_tunnel():
    cf_exe = find_cloudflared()
    if cf_exe:
        print(f"[OK] 使用 cloudflared: {cf_exe}", flush=True)
        cmd = [cf_exe, "tunnel", "--url", f"http://localhost:{port}"]
        return subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )

    # 4. 回退到 npx
    print("[*] 未找到本地 cloudflared，尝试 npx 临时下载...", flush=True)
    # 确保 npm 全局目录存在
    npm_dir = os.path.join(os.environ.get("APPDATA", ""), "npm")
    os.makedirs(npm_dir, exist_ok=True)
    cmd = f'npx --yes cloudflared tunnel --url http://localhost:{port}'
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", shell=True
    )


print("[*] 正在启动穿透服务...\n", flush=True)

try:
    proc = launch_tunnel()
except Exception as e:
    print(f"[错误] 无法启动穿透: {e}", flush=True)
    print("\n请手动安装 cloudflared:", flush=True)
    print("  winget install Cloudflare.cloudflared", flush=True)
    print("  或从 https://github.com/cloudflare/cloudflared/releases 下载", flush=True)
    input("\n按回车退出...")
    sys.exit(1)

found = False
for line in proc.stdout:
    line_clean = line.strip()
    if line_clean:
        print(line_clean, flush=True)
    m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
    if m and not found:
        found = True
        url = m.group(1)
        print("\n" + "=" * 62, flush=True)
        print(f"  公网访问链接: {url}", flush=True)
        print("=" * 62, flush=True)
        print(f"\n  把上面的链接发给别人，即可在外网访问本系统！", flush=True)
        print(f"  按 Ctrl+C 停止穿透\n", flush=True)

proc.wait()
