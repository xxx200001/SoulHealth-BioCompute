"""EVO2 客户端：变异 vs 参考序列的似然对比打分。

真实模式调用链：
- real + EVO2 服务可达（SOULHEALTH_EVO2_URL 或默认 localhost:8899）：
    ① Ensembl 真实解析 rsID → 染色体位置、等位基因、参考序列 121bp 窗口；
    ② 将 ref_seq / alt_seq 发送到自建 evo2 推理服务打分，返回 ΔlogL。
- real + EVO2 服务不可达：
    仍执行 ①（真数据），status=skipped 如实说明"打分未执行"，
    绝不编造分数。
- mock（显式）：读演示缓存，source=mock_cache 强制标注。

自建 evo2 服务：运行 evo2_server.py（WSL2 中 conda activate evo2 后启动），
端点为 POST /v1/evo2/score，接收 {ref_seq, alt_seq}，返回 {ref_ll, alt_ll, delta_ll}。
"""
from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from typing import Optional

from .. import config
from . import ensembl_client

_TIMEOUT = 45
_RSID = re.compile(r"(rs\d+)")


@lru_cache(maxsize=1)
def _fixtures() -> dict:
    path = config.BIOCOMPUTE_FIXTURES / "evo2_fixtures.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _rsid_of(variant: str) -> Optional[str]:
    m = _RSID.search(variant or "")
    return m.group(1) if m else None


def _evo2_available() -> bool:
    """检查 EVO2 本地服务是否可达。"""
    try:
        url = config.EVO2_URL.rstrip("/")
        # 将 /v1/evo2/score 替换为 /health
        base = url.rsplit("/v1/", 1)[0] if "/v1/" in url else url
        req = urllib.request.Request(f"{base}/health",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


def _real_score(ref_seq: str, alt_seq: str) -> dict:
    """调用自建 evo2 服务，返回 {ref_ll, alt_ll, delta_ll, status}。"""
    payload = json.dumps({"ref_seq": ref_seq, "alt_seq": alt_seq}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # 如果配置了 NVIDIA_API_KEY，也带上（兼容 NIM 代理场景）
    if config.NVIDIA_API_KEY:
        headers["Authorization"] = f"Bearer {config.NVIDIA_API_KEY}"
    req = urllib.request.Request(
        config.EVO2_URL, data=payload, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def score_variant(gene: str, variant: str) -> dict:
    """统一返回：{service, gene, variant, status, chrom, pos, ref, alt,
    window_bp, ref_ll, alt_ll, delta_ll, interpretation, source, note}
    status: done | skipped | error"""
    base = {"service": "evo2", "gene": gene, "variant": variant}
    rsid = _rsid_of(variant)

    # —— 显式 MOCK ——
    if config.BIOCOMPUTE_MODE != "real":
        fx = _fixtures().get(rsid or "")
        if not fx:
            return {**base, "status": "error", "source": "mock_cache",
                    "note": f"演示缓存中无 {variant} 条目"}
        return {**base, "status": "done", "source": "mock_cache",
                "window_bp": fx["window_bp"], "ref_ll": fx["ref_ll"],
                "alt_ll": fx["alt_ll"], "delta_ll": fx["delta_ll"],
                "percentile": fx.get("percentile"),
                "interpretation": fx["interpretation"],
                "note": "演示缓存数据，仅用于离线演示"}

    # —— 真实模式 ——
    if not rsid:
        return {**base, "status": "error", "source": "ensembl",
                "note": "变异标识中未找到 rsID，无法定位基因组位置"}

    win, err = ensembl_client.variant_windows(rsid)
    if win is None:
        return {**base, "status": "error", "source": "ensembl", "note": err}

    loc = {"chrom": win["chrom"], "pos": win["pos"], "ref": win["ref"],
           "alt": win["alt"], "assembly": win["assembly"],
           "window_bp": win["window_bp"]}

    if not _evo2_available():
        return {**base, **loc, "status": "skipped", "source": "ensembl",
                "note": "变异位置与等位基因为 Ensembl 实时数据；"
                        "EVO2 推理服务不可达（请在 WSL2 中启动 evo2_server.py），"
                        "序列打分未执行，不以演示分数代替"}

    try:
        result = _real_score(win["ref_seq"], win["alt_seq"])
        ref_ll = result.get("ref_ll")
        alt_ll = result.get("alt_ll")
        delta = result.get("delta_ll")
        if ref_ll is None or alt_ll is None:
            return {**base, **loc, "status": "error", "source": "evo2_local",
                    "note": "EVO2 服务返回的响应中缺少 ref_ll / alt_ll 字段"}
        if delta is None:
            delta = round(alt_ll - ref_ll, 4)
        direction = "低于" if delta < 0 else ("高于" if delta > 0 else "等于")
        interp = (f"变异序列（{win['ref']}→{win['alt']}）在真实基因组上下文"
                  f"（chr{win['chrom']}:{win['pos']}，{win['window_bp']}bp 窗口）中的"
                  f"模型似然{direction}参考序列（Δ logL = {delta}）。"
                  "该分值为序列层面的辅助参考，是否携带该变异需基因检测确认")
        scored = {**base, **loc, "status": "done", "source": "evo2_local+ensembl",
                  "ref_ll": round(ref_ll, 4), "alt_ll": round(alt_ll, 4),
                  "delta_ll": round(delta, 4), "interpretation": interp,
                  "note": win.get("note")}
        return scored
    except Exception as exc:
        return {**base, **loc, "status": "error", "source": "evo2_local",
                "note": f"EVO2 推理服务请求失败：{exc}"}
