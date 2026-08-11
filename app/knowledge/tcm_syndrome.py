"""中医证型识别（阶段五扩充）：从患者自述症状文本中提取失眠/咽喉不适/气虚等
食养相关证型标签，用于丰富组方引擎的可覆盖场景。

与 rules.py 的生物医学风险标签严格区分：
- 风险标签（risk_tags）基于化验/影像数值，写入《健康分析报告》"健康风险识别"章节；
- 本模块产出的证型标签（syndrome_tags）基于患者自述关键词，**不作为医学诊断**，
  仅供组方引擎与《代茶饮建议》的辨证参考章节使用，报告中会明确标注"自述、非诊断"。

识别方式是关键词匹配（可审计、可解释），不是模型推断——同样遵循"真实优先、
不臆造"的原则：没有自述文本就不产生任何证型标签。
"""
from __future__ import annotations

from typing import List

# 每组：证型 id → (显示名, 关键词列表)。关键词覆盖口语与书面表达。
SYNDROME_KEYWORDS = {
    "insomnia_pattern": {
        "label": "失眠 / 睡眠不佳",
        "keywords": ["失眠", "入睡困难", "睡不着", "多梦", "易醒", "睡眠不好",
                    "难以入睡", "睡眠差", "浅眠", "早醒", "睡不好", "睡眠质量差"],
    },
    "throat_pattern": {
        "label": "咽喉不适",
        "keywords": ["咽干", "咽痛", "咽喉不适", "嗓子疼", "嗓子痛", "声音嘶哑",
                    "喉咙痛", "咽喉肿痛", "嗓子干", "咽部异物感", "咽喉痒"],
    },
    "qi_deficiency_pattern": {
        "label": "气虚 / 疲乏",
        "keywords": ["乏力", "气短", "疲乏", "容易累", "精神不振", "气虚",
                    "懒言", "易疲劳", "浑身没劲", "没精神", "犯困", "倦怠"],
    },
    "damp_heat_pattern": {
        "label": "湿热内蕴倾向",
        "keywords": ["口苦", "口黏", "舌苔黄腻", "苔黄腻", "小便黄", "尿黄",
                    "大便黏", "大便粘", "身重困倦", "口臭", "痘痘多", "长痘",
                    "面部油腻", "头油"],
    },
    "yin_deficiency_pattern": {
        "label": "阴虚津亏倾向",
        "keywords": ["口干", "咽干", "口渴", "盗汗", "夜间出汗", "手足心热",
                    "五心烦热", "潮热", "眼干", "皮肤干燥", "干咳无痰", "舌红少苔"],
    },
    "constipation_pattern": {
        "label": "肠燥便秘倾向",
        "keywords": ["便秘", "大便干", "大便干结", "排便困难", "几天一次大便",
                    "排便费力", "大便硬"],
    },
    "wind_heat_pattern": {
        "label": "风热外感初起倾向",
        "keywords": ["嗓子疼发烧", "咽痛发热", "流黄鼻涕", "黄涕", "感冒喉咙痛",
                    "风热感冒", "发热咽痛", "咽喉红肿"],
    },
    "spleen_damp_pattern": {
        "label": "脾虚湿盛倾向",
        "keywords": ["便溏", "大便稀", "大便不成形", "食欲差", "吃不下饭",
                    "饭后腹胀", "腹胀", "舌有齿痕", "齿痕舌", "消化不好", "拉肚子"],
    },
    "cough_phlegm_pattern": {
        "label": "咳嗽有痰倾向",
        "keywords": ["咳嗽", "咳痰", "有痰", "痰多", "白痰", "嗓子有痰", "咳个不停"],
    },
    "summer_damp_pattern": {
        "label": "暑湿困表倾向",
        "keywords": ["中暑", "暑天头晕", "夏天没胃口", "苦夏", "暑湿",
                    "夏天浑身没劲", "头身困重"],
    },
}

# 证型互斥：便秘方（润肠滑利）与便溏证（需固涩健脾）药性相反，同时命中时
# 以安全优先——保留脾虚湿盛、剔除便秘证型，避免给便溏者润肠通便之品。
MUTUAL_EXCLUSIONS = [
    ("constipation_pattern", "spleen_damp_pattern"),  # (被剔除方, 保留方)
]


def detect(notes: List[str]) -> List[dict]:
    """从自述文本列表中识别证型。返回 [{id, label, matched_keywords, evidence}]。
    无自述文本或无关键词命中时返回空列表（不臆造证型）。"""
    if not notes:
        return []
    joined_notes = [(n or "").strip() for n in notes if (n or "").strip()]
    if not joined_notes:
        return []

    results: List[dict] = []
    for sid, spec in SYNDROME_KEYWORDS.items():
        matched: List[str] = []
        evidence: List[str] = []
        for note in joined_notes:
            hit = [kw for kw in spec["keywords"] if kw in note]
            if hit:
                matched.extend(hit)
                preview = note if len(note) <= 30 else note[:30] + "…"
                evidence.append(f"自述「{preview}」命中「{'、'.join(sorted(set(hit)))}」")
        if matched:
            results.append({
                "id": sid, "label": spec["label"],
                "matched_keywords": sorted(set(matched)),
                "evidence": evidence,
            })

    # 互斥后处理：药性相反的证型同现时按安全优先原则取舍并留痕
    ids = {r["id"] for r in results}
    for drop, keep in MUTUAL_EXCLUSIONS:
        if drop in ids and keep in ids:
            results = [r for r in results if r["id"] != drop]
            for r in results:
                if r["id"] == keep:
                    r["evidence"].append(
                        f"注：自述同时出现便秘与便溏类描述，药性相反；按安全优先"
                        f"保留「{r['label']}」方向、不予润肠通便配伍，请就诊明确")
    return results
