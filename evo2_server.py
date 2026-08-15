"""EVO2 本地推理服务（运行在 WSL2 / Linux 上，包装 evo2 Python 库）。

用法（在 WSL2 Ubuntu 终端中）：
    conda activate evo2
    python evo2_server.py [--port 8899] [--host 0.0.0.0]

首次启动会自动从 HuggingFace 下载 evo2_7b 权重（~14GB），
或从 EVO2_MODEL_DIR 环境变量指定的本地目录加载。

API 端点：
    POST /v1/evo2/score
    Body: {"ref_seq": "ACGT...", "alt_seq": "ACGT..."}
    Response: {"ref_ll": -123.4, "alt_ll": -125.6, "delta_ll": -2.2, "status": "done"}

    GET /health
    Response: {"status": "ok", "model": "evo2_7b", "device": "cuda:0"}
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evo2_server")

# ---------------------------------------------------------------------------
# 延迟导入 evo2（加载模型很慢，服务器启动后才做）
# ---------------------------------------------------------------------------
_model = None
_model_name = os.getenv("EVO2_MODEL_NAME", "evo2_7b")


def _load_model():
    """首次请求时（或启动时）加载模型到 GPU。"""
    global _model
    if _model is not None:
        return _model

    log.info("正在加载 EVO2 模型 '%s' ...", _model_name)
    t0 = time.time()

    try:
        from evo2 import Evo2
        model_dir = os.getenv("EVO2_MODEL_DIR", "")
        if model_dir:
            _model = Evo2(model_dir)
            log.info("从本地目录加载: %s", model_dir)
        else:
            _model = Evo2(_model_name)
            log.info("从 HuggingFace 加载: %s", _model_name)
    except Exception as exc:
        log.error("EVO2 模型加载失败: %s", exc)
        raise

    elapsed = time.time() - t0
    log.info("EVO2 模型加载完成，耗时 %.1f 秒", elapsed)
    return _model


def _score_sequence(seq: str) -> float:
    """对单条 DNA 序列计算 log-likelihood（前向传播）。"""
    import torch
    model = _load_model()

    # evo2 库的推理接口：前向传播获取 logits → 计算序列的 log-likelihood
    # 根据 evo2 官方 API，使用 model() 前向或 model.score() 打分
    try:
        # 方式一：如果 evo2 提供 score 方法
        if hasattr(model, 'score'):
            result = model.score(seq)
            if isinstance(result, (int, float)):
                return float(result)
            if isinstance(result, dict):
                for k in ('logprob', 'log_likelihood', 'score', 'll'):
                    if k in result:
                        return float(result[k])
            # tensor
            if hasattr(result, 'item'):
                return float(result.item())
            return float(result)

        # 方式二：手动前向计算 log-likelihood
        import torch
        with torch.no_grad():
            # tokenize
            if hasattr(model, 'tokenizer'):
                tokens = model.tokenizer.tokenize(seq)
                if hasattr(tokens, 'to'):
                    tokens = tokens.to(model.device if hasattr(model, 'device') else 'cuda')
                outputs = model(tokens, return_embeddings=False)
            else:
                outputs = model(seq)

            # 从输出提取 logits 或 log-likelihood
            if isinstance(outputs, tuple):
                logits = outputs[0]
            elif isinstance(outputs, dict):
                logits = outputs.get('logits', outputs.get('output'))
            else:
                logits = outputs

            if hasattr(logits, 'shape') and len(logits.shape) >= 2:
                # logits shape: [1, seq_len, vocab_size] 或 [seq_len, vocab_size]
                import torch.nn.functional as F
                if len(logits.shape) == 3:
                    logits = logits[0]  # [seq_len, vocab_size]
                log_probs = F.log_softmax(logits[:-1], dim=-1)

                # 将序列转为 token IDs 用于索引
                base_to_idx = {'A': 0, 'C': 1, 'G': 2, 'T': 3}
                total_ll = 0.0
                for i, base in enumerate(seq[1:]):
                    idx = base_to_idx.get(base.upper(), 0)
                    if i < log_probs.shape[0]:
                        total_ll += log_probs[i, idx].item()
                return total_ll
            else:
                return float(logits) if logits is not None else 0.0

    except Exception as exc:
        log.error("序列打分失败: %s", exc)
        raise


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError:
    log.error("需要安装 fastapi 和 uvicorn：pip install fastapi uvicorn")
    sys.exit(1)

app = FastAPI(title="EVO2 Local Inference Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


class ScoreRequest(BaseModel):
    ref_seq: str
    alt_seq: str


class ScoreResponse(BaseModel):
    ref_ll: float
    alt_ll: float
    delta_ll: float
    status: str = "done"
    model: str = ""
    note: str = ""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": _model_name,
        "model_loaded": _model is not None,
        "device": "cuda" if _model is not None else "pending",
    }


@app.post("/v1/evo2/score", response_model=ScoreResponse)
def score_variant(req: ScoreRequest):
    if not req.ref_seq or not req.alt_seq:
        raise HTTPException(400, "ref_seq 和 alt_seq 不能为空")
    if len(req.ref_seq) != len(req.alt_seq):
        raise HTTPException(400, f"ref_seq({len(req.ref_seq)}) 和 alt_seq({len(req.alt_seq)}) 长度不一致")

    try:
        ref_ll = _score_sequence(req.ref_seq)
        alt_ll = _score_sequence(req.alt_seq)
        delta = round(alt_ll - ref_ll, 4)
        return ScoreResponse(
            ref_ll=round(ref_ll, 4),
            alt_ll=round(alt_ll, 4),
            delta_ll=delta,
            status="done",
            model=_model_name,
        )
    except Exception as exc:
        log.exception("打分失败")
        raise HTTPException(500, f"EVO2 打分失败: {exc}")


@app.on_event("startup")
async def startup_preload():
    """服务启动时预加载模型（避免首次请求等太久）。"""
    preload = os.getenv("EVO2_PRELOAD", "1").strip()
    if preload == "1":
        log.info("预加载 EVO2 模型...")
        try:
            _load_model()
        except Exception:
            log.warning("预加载失败，首次请求时重试")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EVO2 本地推理服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8899, help="监听端口")
    parser.add_argument("--no-preload", action="store_true",
                        help="不在启动时预加载模型")
    args = parser.parse_args()

    if args.no_preload:
        os.environ["EVO2_PRELOAD"] = "0"

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
