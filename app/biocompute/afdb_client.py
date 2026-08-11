"""AlphaFold DB 客户端：按 UniProt 号真实检索蛋白预测结构（含平均 pLDDT）。

阶段五语义：默认真实调用（EMBL-EBI 公开接口、免密钥）。
- real（默认）：GET {AFDB_API}{uniprot}；未硬编码 UniProt 的基因先经
  UniProt REST 在线解析（人类 + 已审校）。失败即 status=error 并附原因，
  绝不回落到演示数据冒充结果。
- mock（显式 SOULHEALTH_BIOCOMPUTE=mock）：读本地演示缓存，source=mock_cache，
  报告与前端强制标注「演示缓存」。
仅标准库 urllib，零三方依赖。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Optional

from .. import config

_TIMEOUT = 20


@lru_cache(maxsize=1)
def _fixtures() -> dict:
    path = config.BIOCOMPUTE_FIXTURES / "afdb_fixtures.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _http_json(url: str, headers: Optional[dict] = None):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_uniprot(gene: str) -> Optional[str]:
    """基因名 → UniProt 号（人类、已审校，取首条）。真实在线解析。"""
    query = urllib.parse.urlencode({
        "query": f"gene_exact:{gene} AND organism_id:9606 AND reviewed:true",
        "fields": "accession", "format": "json", "size": "1",
    })
    try:
        data = _http_json(f"{config.UNIPROT_API}?{query}")
        results = data.get("results") or []
        return results[0]["primaryAccession"] if results else None
    except Exception:
        return None


def fetch_structure(gene: str, uniprot: Optional[str]) -> dict:
    """统一返回：{service, gene, uniprot, status, mean_plddt, entry_id,
    page_url, model_url, source, note}；status: done | error | pending_resolution"""
    base = {"service": "alphafold_db", "gene": gene, "uniprot": uniprot}

    # —— 显式 MOCK：演示缓存，强制标注 ——
    if config.BIOCOMPUTE_MODE != "real":
        if not uniprot:
            return {**base, "status": "pending_resolution", "source": "mock_cache",
                    "note": "MOCK 模式不联网；真实模式将经 UniProt REST 在线解析"}
        fx = _fixtures().get(uniprot)
        if not fx:
            return {**base, "status": "error", "source": "mock_cache",
                    "note": f"演示缓存中无 {uniprot}；真实模式将从 AlphaFold DB 拉取"}
        return {**base, "status": "done", "source": "mock_cache",
                "mean_plddt": fx["mean_plddt"], "entry_id": fx["entry_id"],
                "page_url": fx["page_url"], "model_url": fx["model_url"],
                "note": "演示缓存数据，仅用于离线演示"}

    # —— 真实模式（默认） ——
    resolved_note = None
    if not uniprot:
        uniprot = resolve_uniprot(gene)
        if uniprot:
            base["uniprot"] = uniprot
            resolved_note = "UniProt 号已在线解析"
        else:
            return {**base, "status": "error", "source": "uniprot_api",
                    "note": f"UniProt 在线解析 {gene} 失败（网络或无匹配），"
                            "本项未执行；不使用演示数据代替"}
    try:
        data = _http_json(f"{config.AFDB_API}{uniprot}")
        entry = data[0] if isinstance(data, list) and data else {}
        if not entry:
            return {**base, "status": "error", "source": "afdb_api",
                    "note": "AlphaFold DB 无该条目"}
        return {**base, "status": "done", "source": "afdb_api",
                "mean_plddt": entry.get("globalMetricValue"),
                "entry_id": entry.get("entryId"),
                "page_url": f"https://alphafold.ebi.ac.uk/entry/{uniprot}",
                "model_url": entry.get("cifUrl") or entry.get("pdbUrl"),
                "note": resolved_note}
    except Exception as exc:
        return {**base, "status": "error", "source": "afdb_api",
                "note": f"AlphaFold DB 请求失败：{exc}（请检查服务器外网连通性）"}
