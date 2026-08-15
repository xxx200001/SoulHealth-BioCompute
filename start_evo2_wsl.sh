#!/bin/bash
# ===================================================================
# EVO2 推理服务 WSL2 启动脚本
# 
# 用法：在 WSL2 终端中执行
#   chmod +x start_evo2_wsl.sh
#   ./start_evo2_wsl.sh
#
# 环境要求：
#   - conda 环境 "evo2" 已安装 evo2, torch, flash-attn
#   - GPU 驱动正常（nvidia-smi 可用）
#   - 模型权重已下载（首次自动下载或指定 EVO2_MODEL_DIR）
# ===================================================================

set -e

echo "=========================================="
echo "  EVO2 推理服务启动"
echo "=========================================="

# 激活 conda 环境
CONDA_BASE=$(conda info --base 2>/dev/null || echo "$HOME/miniconda3")
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate evo2

echo "[1/3] 检查 GPU..."
if command -v nvidia-smi &>/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
    echo "警告：nvidia-smi 不可用，模型将使用 CPU（极慢）"
fi

echo "[2/3] 检查 Python 依赖..."
python -c "import evo2; print(f'evo2 版本: {evo2.__version__ if hasattr(evo2, \"__version__\") else \"installed\"}')" 2>/dev/null || {
    echo "错误：evo2 未安装，请执行 pip install evo2"
    exit 1
}
python -c "import fastapi, uvicorn; print('fastapi + uvicorn: OK')" 2>/dev/null || {
    echo "安装 fastapi + uvicorn..."
    pip install fastapi uvicorn
}

echo "[3/3] 启动 EVO2 推理服务 (端口 8899)..."
echo ""
echo "服务地址: http://localhost:8899"
echo "健康检查: http://localhost:8899/health"
echo "打分端点: POST http://localhost:8899/v1/evo2/score"
echo ""
echo "按 Ctrl+C 停止服务"
echo "=========================================="

# 如果模型在本地目录，取消下面注释并修改路径
# export EVO2_MODEL_DIR="$HOME/evo2_7b_model"

# 启动服务（预加载模型）
cd "$(dirname "$0")"
python evo2_server.py --host 0.0.0.0 --port 8899
