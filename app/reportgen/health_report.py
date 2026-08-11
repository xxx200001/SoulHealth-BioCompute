"""《个性化健康分析报告》生成器：对齐需求文档 Step 4 的四段式结构 ——
健康风险分析、机制解释（临床数据→基因/蛋白→生物机制→风险方向）、
生物计算辅助分析、健康管理建议，另附档案数据与免责声明。
"""
from __future__ import annotations

from typing import List

from .. import config
from ..knowledge import kb

_SEVERITY = {"info": "提示", "watch": "关注", "high": "建议就医评估"}


def build_blocks(ctx: dict) -> List[tuple]:
    snapshot, risk_tags = ctx["snapshot"], ctx["risk_tags"]
    chain, bioplan = ctx["mechanism_chain"], ctx["biocompute_plan"]
    p = snapshot["patient"]
    sex = {"female": "女", "male": "男"}.get(p.get("sex"), "未录")

    blocks: List[tuple] = [
        ("title", "个性化健康分析报告（Demo）"),
        ("p", [("分析编号：", True), (ctx["analysis_id"], False),
               ("　生成时间：", True), (snapshot["generated_at"], False)]),
        ("note", "本报告由 AI 健康 Agent 基于用户健康档案自动生成，定位为健康管理辅助，"
                 "不替代医生诊断；请结合线下诊疗与随访使用。档案数据已脱敏。"),

        ("h1", "一、档案摘要"),
        ("p", f"{(p['name'] + '（' + p['pseudonym'] + '）') if (config.REPORT_REAL_NAME and p.get('name')) else p['pseudonym']}："
              f"{sex}，{p.get('age_years', '—')} 岁；"
              f"身高 {p.get('height_cm') or '—'}cm，体重 {p.get('weight_kg') or '—'}kg。"),
        ("table", {
            "header": ["资料", "类型", "检查日期", "抽取引擎"],
            "rows": [[d["source_filename"] or d["id"][:8],
                      {"ultrasound_report": "超声报告", "lab_report": "化验单",
                       "clinical_note": "病历", "other": "其他"}.get(d["doc_type"], d["doc_type"]),
                      d["exam_date"] or "—", d["engine"] or "—"]
                     for d in snapshot["documents"]],
        }),
        ("h2", "关键指标（最新值）"),
        ("table", {
            "header": ["指标", "数值", "单位", "参考区间", "标志"],
            "rows": [[f"{o['code']}（{o.get('display') or ''}）",
                      o.get("value_num") if o.get("value_num") is not None else o.get("value_text"),
                      o.get("unit") or "—",
                      (f"{o.get('ref_low')}–{o.get('ref_high')}"
                       if o.get("ref_high") is not None else "—"),
                      {"H": "↑ 偏高", "L": "↓ 偏低", "N": "正常"}.get(o.get("abnormal_flag"), "—")]
                     for o in snapshot["observations_latest"].values()],
        }),
    ]

    interp = ctx.get("interpretation") or {}
    blocks.append(("h2", "AI 综合解读"))
    if interp.get("available"):
        for para in interp["text"].split("\n"):
            if para.strip():
                blocks.append(("p", para.strip()))
        blocks.append(("note", f"本节由大模型（{interp.get('model')}）通读本次全部"
                               "结构化分析结果后生成，已过合规校验；内容为健康管理"
                               "参考，不构成诊断与处方，请以医生意见为准。"))
    else:
        blocks.append(("note", "本次未生成 AI 综合解读——"
                       + (interp.get("reason") or "未启用。")
                       + " 本报告其余章节均为规则引擎的结构化产出，不受影响。"))

    if snapshot.get("notes"):
        blocks.append(("h2", "自述症状 / 主诉记录"))
        for n in snapshot["notes"]:
            blocks.append(("bullet", f"{n['text']}（记录于 {n['created_at'][:10]}）"))
        syn = ctx.get("syndrome_tags") or []
        if syn:
            labels = "、".join(s["label"] for s in syn)
            blocks.append(("bullet", f"系统识别到与「{labels}」相关的自述关键词，"
                                     "已在《药食同源代茶饮建议》中纳入对应食养方向"
                                     "（关键词匹配，非诊断，具体证型请以中医师面诊为准）。"))
        blocks.append(("note", "自述症状为主观信息，本报告不对其作病因判断；"
                               "症状的鉴别需医生面诊结合体格检查与必要检查完成。"))

    if not snapshot.get("documents"):
        blocks.append(("note", "数据充分性提示：本次档案中尚无检查报告或化验单，"
                               "分析仅基于已填写的基础信息与自述内容，覆盖范围有限。"
                               "上传超声/化验等客观检查资料后重新分析，结论会更完整。"))

    if snapshot.get("impressions"):
        blocks.append(("h2", "影像/报告提示"))
        for imp in snapshot["impressions"]:
            blocks.append(("bullet", f"{imp['text']}（{imp['exam_date'] or '日期未录'}）"))

    blocks.append(("h1", "二、健康风险分析"))
    if not risk_tags:
        blocks.append(("p", "本次分析未识别出显著风险标签：现有档案数据未触发任何风险"
                            "规则。这不等于排除所有健康问题，建议保持定期体检随访；"
                            "如有症状请及时就医。"))
    for t in risk_tags:
        blocks.append(("h3", f"{t['label']}　[{_SEVERITY.get(t['severity'], t['severity'])}]"))
        for ev in t["evidence"]:
            blocks.append(("bullet", f"依据：{ev}"))
        if t.get("note"):
            blocks.append(("p", t["note"]))
        know = kb.condition_knowledge(t["id"])
        if know:
            for point in know["points"][:2]:
                blocks.append(("bullet", point))

    blocks.append(("h1", "三、机制解释：临床数据 → 基因/蛋白 → 生物机制 → 风险方向"))
    blocks.append(("p", chain["note"]))
    for level in chain["levels"]:
        if not level["items"]:
            continue
        blocks.append(("h2", level["level"]))
        for item in level["items"]:
            blocks.append(("bullet", item))
    if chain["entities"]:
        blocks.append(("h2", "机制相关蛋白一览"))
        blocks.append(("table", {
            "header": ["基因", "蛋白", "UniProt", "代表性变异", "机制角色"],
            "rows": [[e["gene"], e["protein"], e.get("uniprot") or "待在线解析",
                      e.get("variant") or "—", e["rationale"]]
                     for e in chain["entities"]],
        }))

    blocks.append(("h1", "四、生物计算辅助分析"))
    if bioplan:
        mock_all = all(b.get("source") == "mock_cache" for b in bioplan)
        blocks.append(("p", "AI Agent 判定本次分析涉及蛋白/序列层面机制，已调用生物计算服务："
                            "AlphaFold DB（蛋白预测结构与 pLDDT 置信度）、"
                            "EVO2（序列层面变异效应演示打分）。"))
        if mock_all:
            blocks.append(("note", "当前为演示缓存数据（MOCK 模式），字段结构与真实服务一致，"
                                   "仅用于流程演示；切换真实模式后本表自动回填在线结果。"))

        def _result(b: dict) -> str:
            if b.get("status") == "done" and b["service"] == "alphafold_db":
                return f"平均 pLDDT {b.get('mean_plddt')}（{b.get('entry_id')}）"
            if b.get("status") == "done" and b["service"] == "evo2":
                loc = (f"，chr{b.get('chrom')}:{b.get('pos')}"
                       if b.get("chrom") else "")
                pct = (f"，演示背景第 {b.get('percentile')} 百分位"
                       if b.get("percentile") is not None else "")
                return f"Δ logL {b.get('delta_ll')}（变异 vs 参考{loc}{pct}）"
            if b.get("status") == "skipped":
                loc = (f"位点 chr{b.get('chrom')}:{b.get('pos')} "
                       f"{b.get('ref')}>{b.get('alt')}（Ensembl 实时数据）；"
                       if b.get("chrom") else "")
                return loc + "序列打分未执行（未配置 NVIDIA_API_KEY）"
            if b.get("status") == "pending_resolution":
                return "待 UniProt 在线解析（真实模式执行）"
            return f"未完成：{b.get('note') or '未知原因'}"

        blocks.append(("table", {
            "header": ["计算服务", "对象", "关键结果", "数据来源"],
            "rows": [[b["service"],
                      " / ".join(str(v) for v in b["target"].values() if v),
                      _result(b),
                      {"mock_cache": "演示缓存", "afdb_api": "AlphaFold DB API",
                       "nim": "EVO2 服务", "nim+ensembl": "EVO2 + Ensembl",
                       "ensembl": "Ensembl API",
                       "uniprot_api": "UniProt API"}.get(
                           b.get("source"), "—")]
                     for b in bioplan],
        }))
        evo_done = [b for b in bioplan if b["service"] == "evo2"
                    and b.get("status") == "done" and b.get("interpretation")]
        if evo_done:
            blocks.append(("h2", "序列层解读（演示）"))
            for b in evo_done:
                blocks.append(("bullet", f"{b['gene']} {b['variant']}：{b['interpretation']}"))
        afdb_done = [b for b in bioplan if b["service"] == "alphafold_db"
                     and b.get("status") == "done"]
        if afdb_done:
            blocks.append(("h2", "结构层入口"))
            for b in afdb_done:
                blocks.append(("bullet", f"{b['gene']}（{b.get('uniprot')}）结构页："
                                          f"{b.get('page_url')}"))
    else:
        blocks.append(("p", "本次分析未涉及需要生物计算辅助的机制层问题。"))

    blocks.append(("h1", "五、健康管理建议"))
    seen: set = set()
    sections = [
        ("生活方式", ["obesity", "overweight", "insulin_resistance_risk",
                     "blood_pressure_high"]),
        ("体重与营养", ["underweight"]),
        ("饮食", ["obesity", "nash_possible", "glucose_high", "dyslipidemia",
                 "hyperuricemia"]),
        ("运动", ["insulin_resistance_risk", "obesity", "glucose_high",
                 "dyslipidemia"]),
        ("随访与就医", ["liver_enzyme_elevated", "nash_possible",
                       "pancreatic_steatosis_possible", "fatty_liver_us",
                       "glucose_high", "hyperuricemia", "blood_pressure_high",
                       "renal_flag", "anemia_low_hgb", "imaging_nodule",
                       "imaging_stone", "imaging_cyst", "imaging_polyp",
                       "imaging_mass", "underweight", "symptom_note"]),
    ]
    for title, tag_ids in sections:
        advices: List[str] = []
        for tid in tag_ids:
            know = kb.condition_knowledge(tid)
            if not know or tid not in {t["id"] for t in risk_tags}:
                continue
            for a in know.get("advice", []):
                if a not in seen:
                    advices.append(a)
                    seen.add(a)
        if advices:
            blocks.append(("h2", title))
            for a in advices:
                blocks.append(("bullet", a))
    blocks.append(("h2", "食养配合"))
    if (ctx.get("formula") or {}).get("ingredients"):
        blocks.append(("p", "个性化药食同源代茶饮建议已另行生成（见配套文档），"
                            "作为上述生活方式管理的食养辅助使用。"))
    else:
        blocks.append(("p", "本次未识别出需要食养干预的风险方向，未生成代茶饮建议；"
                            "保持均衡饮食与定期体检即可。"))

    blocks += [
        ("h1", "六、免责声明"),
        ("p", "本报告为健康管理辅助分析，不构成医疗诊断或治疗建议，不替代医师面诊；"
              "风险条目中的「可能/推断」均需临床确认。请携带原始报告至正规医疗机构"
              "就诊与随访，任何用药与治疗调整请遵医嘱。"),
    ]
    return blocks
