"""EVO2 客户端：变异 vs 参考序列的似然对比打分。

阶段五语义（真实优先、诚实降级）：
- real（默认）+ 有 NVIDIA_API_KEY：
    ① Ensembl 真实解析 rsID → 染色体位置、等位基因、参考序列 121bp 窗口；
    ② 用真实 ref/alt 序列各做一次 EVO2 前向（NVIDIA NIM），返回 ΔlogL。
- real + 无 NVIDIA_API_KEY：
    仍执行 ①（真数据），status=skipped 如实说明"打分未执行（缺 key）"，
    绝不编造分数。
- mock（显式）：读演示缓存，source=mock_cache 强制标注。
不同 NIM 版本响应字段可能不同：_ll() 做防御式解析，结构不符时
status=error 并指向需要调整的位置。
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


def _real_call(sequence: str) -> dict:
    payload = json.dumps({"sequence": sequence, "num_tokens": 1,
                          "temperature": 0.0}).encode("utf-8")
    req = urllib.request.Request(
        config.EVO2_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.NVIDIA_API_KEY}"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ll(obj) -> Optional[float]:
    """从 NIM 响应中提取似然类字段（不同版本字段名有差异，逐一尝试）。"""
    if not isinstance(obj, dict):
        return None
    for key in ("logprob", "log_likelihood", "sequence_logprob", "score"):
        v = obj.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    # 嵌套一层（如 {"output": {...}} / {"choices":[{...}]}）
    for key in ("output", "result", "data"):
        v = _ll(obj.get(key))
        if v is not None:
            return v
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        return _ll(choices[0])
    return None


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

    if not config.NVIDIA_API_KEY:
        return {**base, **loc, "status": "skipped", "source": "ensembl",
                "note": "变异位置与等位基因为 Ensembl 实时数据；"
                        "序列打分未执行（未配置 NVIDIA_API_KEY），不以演示分数代替"}

    try:
        out_ref, out_alt = _real_call(win["ref_seq"]), _real_call(win["alt_seq"])
        ref_ll, alt_ll = _ll(out_ref), _ll(out_alt)
        if ref_ll is None or alt_ll is None:
            return {**base, **loc, "status": "error", "source": "nim",
                    "note": "EVO2 响应结构与预期不同：请按所用 NIM 版本文档调整 "
                            "app/biocompute/evo2_client.py 的 _real_call/_ll 解析"}
        delta = round(alt_ll - ref_ll, 4)
        direction = "低于" if delta < 0 else ("高于" if delta > 0 else "等于")
        interp = (f"变异序列（{win['ref']}→{win['alt']}）在真实基因组上下文"
                  f"（chr{win['chrom']}:{win['pos']}，{win['window_bp']}bp 窗口）中的"
                  f"模型似然{direction}参考序列（Δ logL = {delta}）。"
                  "该分值为序列层面的辅助参考，是否携带该变异需基因检测确认")
        result = {**base, **loc, "status": "done", "source": "nim+ensembl",
                  "ref_ll": round(ref_ll, 4), "alt_ll": round(alt_ll, 4),
                  "delta_ll": delta, "interpretation": interp,
                  "note": win.get("note")}
        return result
    except Exception as exc:
        return {**base, **loc, "status": "error", "source": "nim",
                "note": f"EVO2 请求失败：{exc}"}
