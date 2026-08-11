"""AI 健康 Agent 编排器：轻量状态机（约百行、零依赖、逻辑可讲解）。

节点：LOAD_SNAPSHOT → IDENTIFY_RISKS → MATCH_KNOWLEDGE →
      PLAN_BIOCOMPUTE → GENERATE_REPORTS
每个节点产出一条 trace（step / title / detail / ms），供前端把
"分析过程"逐步可视化（阶段四）。接口设计与 LangGraph 节点同构，
后续如需迁移编排框架可平滑替换。
"""
from __future__ import annotations

import time
from typing import Callable, List

from ..archive import repository as repo
from ..knowledge import formula as formula_kb
from ..biocompute import runner as bio_runner
from ..reportgen import generator
from ..knowledge import tcm_syndrome
from . import interpretation, mechanism, rules


def _trace(steps: List[dict], step: str, title: str, detail: str, t0: float) -> None:
    steps.append({"step": step, "title": title, "detail": detail,
                  "ms": round((time.time() - t0) * 1000, 1)})


def run_analysis(patient_id: str,
                 on_step: Callable[[dict], None] | None = None) -> dict:
    trace: List[dict] = []

    def mark(step: str, title: str, detail: str, t0: float) -> None:
        _trace(trace, step, title, detail, t0)
        if on_step:
            on_step(trace[-1])

    # 1) 档案快照（历史数据持续调用）
    t0 = time.time()
    snapshot = repo.snapshot(patient_id)
    mark("LOAD_SNAPSHOT", "载入健康档案快照",
         f"文档 {len(snapshot['documents'])} 份，指标 "
         f"{len(snapshot['observations_timeline'])} 条，影像所见 "
         f"{len(snapshot['findings'])} 项", t0)

    # 2) 风险识别（显式规则，可审计）
    t0 = time.time()
    risk_tags = rules.identify_risks(snapshot)
    mark("IDENTIFY_RISKS", "疾病/风险识别",
         "识别出：" + ("；".join(t["label"] for t in risk_tags) or "无显著风险标签"), t0)

    # 3) 医学知识匹配：证型识别 → 机制链（证型也触发病理生理层）→ 配方
    t0 = time.time()
    # 证型识别仅基于自述文本关键词，与化验/影像驱动的风险标签严格分开；
    # 用于组方引擎与机制链的病理生理层触发，不写入"健康风险识别"章节、不构成诊断。
    syndrome_tags = tcm_syndrome.detect([n["text"] for n in snapshot.get("notes", [])])
    chain = mechanism.build_chain(risk_tags, snapshot, syndrome_tags=syndrome_tags)
    formula_ids = [t["id"] for t in risk_tags] + [s["id"] for s in syndrome_tags]
    formula = formula_kb.build_formula(
        formula_ids, sex=snapshot["patient"].get("sex") or "unknown")
    sub_note = ("；目录门禁替换 " +
                "、".join(f"{s['original']}→{s['replaced_by']}"
                          for s in formula["substitutions"])
                ) if formula["substitutions"] else ""
    syn_note = ("；识别到自述证型 " + "、".join(s["label"] for s in syndrome_tags)
               ) if syndrome_tags else ""
    mark("MATCH_KNOWLEDGE", "医学知识匹配",
         f"机制实体 {len(chain['entities'])} 个；组方 "
         f"{len(formula['ingredients'])} 味{sub_note}{syn_note}", t0)

    # 4) 生物计算调用判断（阶段四执行）
    t0 = time.time()
    bioplan = mechanism.plan_biocompute(chain)
    mark("PLAN_BIOCOMPUTE", "生物计算调用判断",
         ("生成调用计划 " + "、".join(sorted({b['service'] for b in bioplan}))
          + f" 共 {len(bioplan)} 项") if bioplan else "本次无需生物计算辅助", t0)

    # 4.5) 生物计算执行（MOCK 演示缓存 / 真实 API 由配置切换）
    t0 = time.time()
    bioplan = bio_runner.execute_plan(bioplan)
    done = sum(1 for b in bioplan if b.get("status") == "done")
    pend = sum(1 for b in bioplan if b.get("status") == "pending_resolution")
    err = sum(1 for b in bioplan if b.get("status") == "error")
    mark("EXEC_BIOCOMPUTE", "生物计算执行",
         (f"完成 {done} 项" + (f"，待在线解析 {pend} 项" if pend else "")
          + (f"，失败 {err} 项" if err else "")
          + f"（{'演示缓存' if all(b.get('source')=='mock_cache' for b in bioplan) else '真实服务'}）")
         if bioplan else chain.get("biocompute_applicability", "无计划项"), t0)

    # 4.8) AI 综合解读（真实模型通读结构化结果；MOCK/无密钥时如实标注不可用）
    t0 = time.time()
    interp = interpretation.generate(snapshot, risk_tags, chain, formula,
                                     syndrome_tags)
    mark("AI_INTERPRET", "AI 综合解读",
         (f"已生成（{interp['model']}，{len(interp['text'])} 字，过合规校验）"
          if interp.get("available")
          else f"未生成：{interp.get('reason', '')[:64]}…"), t0)

    # 5) 入库 + 报告生成（合规闸内置于 generator）
    t0 = time.time()
    analysis_id = repo.save_analysis(patient_id, snapshot, risk_tags, chain,
                                     bioplan, formula=formula,
                                     syndrome_tags=syndrome_tags,
                                     interpretation=interp)
    ctx = {"analysis_id": analysis_id, "patient_id": patient_id,
           "snapshot": snapshot, "risk_tags": risk_tags,
           "mechanism_chain": chain, "biocompute_plan": bioplan,
           "formula": formula, "syndrome_tags": syndrome_tags,
           "interpretation": interp}
    reports = generator.generate_all(ctx)
    titles = "》《".join(dict.fromkeys(r["title"] for r in reports))
    mark("GENERATE_REPORTS", "最终报告生成",
         f"《{titles}》共 {len(reports)} 个文件（docx+md），均通过合规校验"
         + ("" if formula["ingredients"] else "；无风险标签，本次不生成代茶饮建议"), t0)
    repo.update_analysis_trace(analysis_id, trace)  # 历史分析可完整回放

    return {"analysis_id": analysis_id, "patient_id": patient_id,
            "risk_tags": risk_tags, "mechanism_chain": chain,
            "biocompute_plan": bioplan, "formula": formula,
            "syndrome_tags": syndrome_tags, "interpretation": interp,
            "reports": reports, "trace": trace}
