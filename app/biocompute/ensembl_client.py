"""Ensembl REST 客户端：按 rsID 真实查询变异位点、等位基因与参考序列窗口。

- GET {ENSEMBL_API}/variation/human/{rsid}   → 染色体位置、allele_string（如 C/G）
- GET {ENSEMBL_API}/sequence/region/human/{chr}:{start}..{end} → 参考序列
公开接口、免密钥。EVO2 打分即使不可用（无 NVIDIA key），变异的
真实基因组位置与等位基因也能如实展示——这部分是真数据，不是演示缓存。

仅标准库 urllib；所有失败路径返回 (None, 原因)，不抛异常、不阻断分析。
"""
from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Optional, Tuple

from .. import config

_TIMEOUT = 20
FLANK = 60  # 变异位点两侧各取 60bp → 121bp 窗口

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def variant_info(rsid: str) -> Tuple[Optional[dict], Optional[str]]:
    """rsID → {chrom, pos, ref, alts, allele_string}（GRCh38）。"""
    try:
        data = _http_json(f"{config.ENSEMBL_API}/variation/human/{rsid}"
                          "?content-type=application/json")
    except Exception as exc:
        return None, f"Ensembl 变异查询失败：{exc}"
    mappings = data.get("mappings") or []
    if not mappings:
        return None, f"Ensembl 无 {rsid} 的基因组映射"
    m = mappings[0]
    alleles = str(m.get("allele_string") or "").split("/")
    if len(alleles) < 2 or len(alleles[0]) != 1 or len(alleles[1]) != 1:
        return None, f"{rsid} 非单碱基替换（{m.get('allele_string')}），演示打分暂不支持"
    return {
        "chrom": str(m.get("seq_region_name")),
        "pos": int(m.get("start")),
        "ref": alleles[0].upper(),
        "alts": [a.upper() for a in alleles[1:]],
        "allele_string": m.get("allele_string"),
        "assembly": m.get("assembly_name") or "GRCh38",
    }, None


def region_sequence(chrom: str, start: int, end: int) -> Tuple[Optional[str], Optional[str]]:
    try:
        data = _http_json(
            f"{config.ENSEMBL_API}/sequence/region/human/{chrom}:{start}..{end}"
            "?content-type=application/json")
    except Exception as exc:
        return None, f"Ensembl 序列获取失败：{exc}"
    seq = (data.get("seq") or "").upper()
    if not seq:
        return None, "Ensembl 返回空序列"
    return seq, None


def variant_windows(rsid: str, flank: int = FLANK) -> Tuple[Optional[dict], Optional[str]]:
    """rsID → 变异位点真实上下文序列窗口（ref_seq / alt_seq）。"""
    info, err = variant_info(rsid)
    if info is None:
        return None, err
    seq, err = region_sequence(info["chrom"], info["pos"] - flank, info["pos"] + flank)
    if seq is None:
        return None, err
    center = flank
    note = None
    if len(seq) <= center:
        return None, "序列窗口长度异常"
    if seq[center] != info["ref"]:
        # 参考碱基与 allele_string 首项不符（链向/多等位等原因），如实标注
        note = (f"窗口中心碱基 {seq[center]} 与 allele_string 首项 {info['ref']} 不一致，"
                "已按基因组正链序列打分")
    alt = info["alts"][0]
    alt_seq = seq[:center] + alt + seq[center + 1:]
    return {
        "rsid": rsid, "chrom": info["chrom"], "pos": info["pos"],
        "ref": info["ref"], "alt": alt, "assembly": info["assembly"],
        "ref_seq": seq, "alt_seq": alt_seq, "window_bp": len(seq),
        "note": note, "source": "ensembl",
    }, None
