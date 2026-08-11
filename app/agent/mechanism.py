"""机制解释链组装：临床数据 → 基因/蛋白 → 生物机制 → 风险方向。

阶段五：实体与通路均由 triggers 驱动——机制实体只有在其 triggers 与本次
风险标签相交时才纳入，通路组同理。因此换一个病种（如糖脂/尿酸患者），
机制链会自动切换到 TCF7L2 / LPL / ABCG2 等对应实体，而不是固定输出
NAFLD 四件套。生物计算调用计划同样随实体自适应，并做总量上限保护。
"""
from __future__ import annotations

from typing import List

from ..knowledge import kb

MAX_BIOCOMPUTE_ITEMS = 8  # 多病种叠加时限制调用规模，保证分析时长可控


def build_chain(risk_tags: List[dict], snapshot: dict,
                syndrome_tags: List[dict] | None = None) -> dict:
    """机制解释链。触发源包括：生物医学风险标签 + 自述证型（阶段六起）。

    机制知识库覆盖两类真实内容：
    - 分子层（mechanism_entities）：仅代谢方向（脂肪肝之 PNPLA3、血糖之 TCF7L2 等）
      有可靠的基因-蛋白机制证据，其余方向不硬凑分子解释；
    - 病理生理层（pathway_groups）：体重偏低（能量负平衡与鉴别方向）、咽喉不适
      （黏膜刺激机制）、失眠（睡眠双进程与过度觉醒）等均为教科书级人群水平机制。

    biocompute_applicability：本次是否适用生物计算及其理由——"不调用的理由"
    也是真实产出，会随机制链持久化并在前端/回放中展示，而不是一句"无调用"。"""
    tag_ids = {t["id"] for t in risk_tags}
    syn_ids = {s["id"] for s in (syndrome_tags or [])}
    trigger_ids = tag_ids | syn_ids
    med = kb.medical()

    clinical_items: List[str] = []
    for t in risk_tags:
        clinical_items.extend(t["evidence"][:1])  # 每标签取最关键一条证据
    for s in (syndrome_tags or []):
        if s.get("evidence"):
            clinical_items.append(s["evidence"][0])

    entities = [e for e in med["mechanism_entities"]
                if set(e.get("triggers", [])) & tag_ids]

    pathways: List[str] = []
    risk_directions: List[str] = []
    for group in med.get("pathway_groups", {}).values():
        if set(group.get("triggers", [])) & trigger_ids:
            for p in group.get("pathways", []):
                if p not in pathways:
                    pathways.append(p)
            for r in group.get("risk_directions", []):
                if r not in risk_directions:
                    risk_directions.append(r)

    # 生物计算适用性判定（有分子实体才适用；不适用时说明理由，不虚构调用）
    if entities:
        bio_note = (f"本次风险涉及 {len(entities)} 个有基因-蛋白机制证据的方向，"
                    "已生成生物计算调用计划（AlphaFold 结构 / Ensembl 位点"
                    + ("/ EVO2 打分" if any("evo2" in e.get("biocompute", [])
                                            for e in entities) else "") + "）。")
    else:
        covered = "、".join([t["label"] for t in risk_tags]
                            + [s["label"] for s in (syndrome_tags or [])]) or "（无）"
        bio_note = ("本次未调用生物计算，原因：当前风险方向（" + covered + "）"
                    "均无成熟的单基因-蛋白机制靶点——AlphaFold 蛋白结构查询与 "
                    "EVO2 变异打分只对脂肪肝（PNPLA3）、血糖（TCF7L2）等有明确"
                    "分子证据的代谢方向有意义。对体重、症状与证型类风险强行调用"
                    "分子工具没有科学依据，本系统不为演示效果虚构调用。")

    return {
        "levels": [
            {"level": "临床数据", "items": clinical_items},
            {"level": "基因 / 蛋白",
             "items": [f"{e['gene']}（{e['protein']}）"
                       + (f"，代表性变异 {e['variant']}" if e.get("variant") else "")
                       for e in entities]},
            {"level": "生物机制", "items": pathways},
            {"level": "风险方向",
             "items": risk_directions or [t["label"] for t in risk_tags]},
        ],
        "entities": entities,
        "biocompute_applicability": bio_note,
        "note": "机制层为人群水平的机制学解释（分子层仅限有可靠证据的代谢方向，"
                "病理生理层覆盖体重/咽喉/睡眠等常见方向），用于理解风险来源；"
                "是否携带相关变异需基因检测确认，本系统未使用任何个人基因数据。",
    }


def plan_biocompute(chain: dict) -> List[dict]:
    """由机制实体推导生物计算调用计划（执行由 biocompute.runner 完成）。"""
    plan: List[dict] = []
    for e in chain.get("entities", []):
        services = e.get("biocompute", [])
        if "alphafold_db" in services:
            plan.append({
                "service": "alphafold_db",
                "purpose": f"检索 {e['gene']} 蛋白预测结构（含 pLDDT 置信度）用于机制可视化",
                "target": {"gene": e["gene"], "uniprot": e.get("uniprot")},
                "status": "planned",
                "note": None if e.get("uniprot") else "UniProt 号将由客户端在线解析",
            })
        if "evo2" in services and e.get("variant"):
            plan.append({
                "service": "evo2",
                "purpose": f"对 {e['gene']} {e['variant']} 做真实基因组上下文的序列层变异打分",
                "target": {"gene": e["gene"], "variant": e["variant"]},
                "status": "planned",
                "note": "位点与序列经 Ensembl 实时解析；打分不涉及用户个人基因数据",
            })
    return plan[:MAX_BIOCOMPUTE_ITEMS]
