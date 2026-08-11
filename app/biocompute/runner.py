"""生物计算执行器：把 Agent 的调用计划逐项执行，结果并回原计划项。

Agent 编排器在 PLAN 后调用 execute_plan()；每项计划保留原 purpose/target，
并入客户端返回的 status / 结果字段 / source（真实 API 或演示缓存）。
任何单项失败不阻断整体分析（status=error 附明确原因）。
"""
from __future__ import annotations

from typing import List

from ..archive import repository as repo
from . import afdb_client, evo2_client


def execute_plan(plan: List[dict]) -> List[dict]:
    executed: List[dict] = []
    for item in plan:
        target = item.get("target", {})
        if item["service"] == "alphafold_db":
            result = afdb_client.fetch_structure(
                gene=target.get("gene", ""), uniprot=target.get("uniprot"))
        elif item["service"] == "evo2":
            result = evo2_client.score_variant(
                gene=target.get("gene", ""), variant=target.get("variant", ""))
        else:
            result = {"status": "error", "note": f"未知服务 {item['service']}"}
        merged = {**item, **result}
        merged.pop("note", None) if merged.get("note") is None else None
        executed.append(merged)
    return executed


def execute_and_store(analysis_id: str) -> List[dict]:
    analysis = repo.get_analysis(analysis_id)
    if analysis is None:
        raise KeyError(f"分析不存在: {analysis_id}")
    executed = execute_plan(analysis.get("biocompute") or [])
    repo.update_analysis_biocompute(analysis_id, executed)
    return executed
