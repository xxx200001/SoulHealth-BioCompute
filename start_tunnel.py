# -*- coding: utf-8 -*-
"""一键 Cloudflare 隧道启动脚本"""
import os
import re
import sys
import subprocess

port = sys.argv[1] if len(sys.argv) > 1 else "9000"

print("==================================================", flush=True)
print(f"  SoulHealth 平台 - 开启公网临时穿透 (端口 {port})", flush=True)
print("==================================================\n", flush=True)
print("[*] 正在启动穿透服务...", flush=True)

def try_launch():
    cmds = [
        f"cloudflared tunnel --url http://localhost:{port}",
        f"npx --yes cloudflared tunnel --url http://localhost:{port}"
    ]
    for cmd in cmds:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                shell=True
            )
            return proc
        except Exception:
            continue
    raise RuntimeError("无法启动穿透进程")

proc = try_launch()
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
        print(f"  🎉 成功生成公网访问链接: {url}", flush=True)
        print("=" * 62 + "\n", flush=True)

proc.wait()
