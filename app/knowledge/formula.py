"""代茶饮组方引擎（阶段六重构）：辨证 → 立法 → 底方 → 加减。

对齐真实中医开方流程，替代旧的"标签→散装药材拼装"槽位模式：

  风险标签 + 自述证型
        ↓
  ① 主证决策（PRIMARY_PRIORITY：外感先解表、急则治标、慢性调理靠后；
     无自述证型但有代谢类风险 → 代谢兜底底方"保和丸化裁"）
        ↓
  ② 选经典底方（classic_formulas.CLASSIC_FORMULAS：桑菊饮/参苓白术散/
     酸枣仁汤/生脉饮/杏苏散/藿香正气/保和丸等化裁，骨架保持不变，
     因目录限制去掉的药味逐条留痕）
        ↓
  ③ 兼证兼风险加减（ADDITION_RULES：候选制、每条附依据；外感主证时
     按「外感忌补」跳过补益之品；最多 MAX_ADDITIONS 条防药味堆叠）
        ↓
  ④ 安全层（目录门禁自动替换留痕 / AVOID_IF 禁忌规避留痕 /
     总味数封顶 MAX_TOTAL / 无主证不出方）

返回结构在旧字段之上新增：formula_name / source / treatment_principle /
primary_syndrome / modification_log（底方选择、化裁、每条加减、每次规避
全部留痕，报告与前端逐条展示——加减有据可循是本次重构的核心要求）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from . import kb
from .classic_formulas import (ADDITION_RULES, CLASSIC_FORMULAS, MAX_ADDITIONS,
                               MAX_TOTAL, METABOLIC_TRIGGERS, PRIMARY_PRIORITY,
                               SYNDROME_TO_FORMULA, TONIC_NAMES)

# 候选 → 与之冲突的风险标签（命中则规避该候选并留痕）
AVOID_IF: Dict[str, Set[str]] = {
    "甘草": {"blood_pressure_high"},           # 甘草酸不宜用于血压偏高者
    "大枣": {"glucose_high", "dyslipidemia"},  # 含糖量较高
    "蜂蜜": {"glucose_high", "dyslipidemia"},  # 游离糖
    "桂圆肉": {"glucose_high"},
    "桑椹": {"glucose_high"},
    "夏枯草": {"underweight"},                 # 苦寒伤胃，体弱消瘦者规避
}

# 芳香后下 / 蜂蜜温调 / 苦寒中病即止 的动态煎服法提示
_AROMATIC = {"薄荷", "紫苏", "藿香", "香薷"}
_COLD_HERBS = {"蒲公英", "栀子", "夏枯草"}

ADJUSTMENT_RULES = [
    {"when": "gastric_acid", "text": "胃酸过多、慢性胃炎者：炒山楂减量至 4g，其余不变；仍不适则停用并咨询医师。"},
    {"when": "loose_stool", "text": "平素便溏者：炒决明子减量至 3g 或去之。"},
    {"when": "hypotension", "text": "低血压者：少量多次饮用，避免一次大量。"},
    {"when": "always", "text": "孕期停用本方；备孕、哺乳期或月经期是否饮用，请咨询中医师后决定。"},
    {"when": "always", "text": "正在服用药物或患慢性病者，饮用前请告知医生，避免与治疗冲突。"},
]


def _display_name(item: dict) -> str:
    """展示名：炮制说明里已包含药名就直接用炮制名，否则用原名。"""
    proc = (item.get("processing") or "").strip()
    name = item["name"]
    return proc if (proc and name.split("（")[0] in proc) else name


def _resolve(name: str, active: Set[str], used: Set[str],
             log: List[str], context: str) -> Optional[dict]:
    """单味原料的安全解析：目录门禁 + AVOID_IF + 去重。不可用时留痕并返回 None。"""
    if name in used:
        return None
    item = kb.get_ingredient(name)
    if item is None:
        log.append(f"规避：{context}候选「{name}」未收录于本地目录，跳过（请人工核对）")
        return None
    if not kb.in_catalog(name):
        log.append(f"目录门禁：{context}候选「{name}」状态为「{item['status']}」，"
                   f"非药食同源目录，按合规基线跳过")
        return None
    hit = AVOID_IF.get(name, set()) & active
    if hit:
        log.append(f"禁忌规避：{context}「{name}」与本次风险（{'、'.join(sorted(hit))}）"
                   f"冲突，按安全联动规则去之")
        return None
    return item


def _pick_primary(active: Set[str]) -> Tuple[Optional[str], Optional[str], str]:
    """主证决策。返回 (底方key, 主证id, 决策说明)。"""
    for sid in PRIMARY_PRIORITY:
        if sid in active:
            fkey = SYNDROME_TO_FORMULA[sid]
            return fkey, sid, (f"主证决策：命中自述证型中按治则优先级"
                               f"（外感先解表、急则治标）取「{sid}」为主证")
    if active & METABOLIC_TRIGGERS:
        return "bao_he_fang", None, ("主证决策：无自述证型，存在代谢方向风险标签，"
                                     "启用消导化浊兜底底方（保和丸化裁）")
    return None, None, "无自述证型且无代谢方向风险标签，不组方"


def build_formula(risk_tag_ids: List[str], sex: str = "unknown") -> dict:
    """辨证→立法→底方→加减 四步组方。

    risk_tag_ids: 生物医学风险标签 id 与自述证型 id 的合并列表（orchestrator 传入）。
    无主证（无证型且无代谢标签，如仅体重偏低/仅自述症状）→ 空方，不硬凑。"""
    active = set(risk_tag_ids)
    log: List[str] = []
    empty = {"ingredients": [], "substitutions": [], "catalog_check": [],
             "safety_notes": [], "adjustments": [], "brew": _brew(),
             "formula_name": None, "source": None, "treatment_principle": None,
             "primary_syndrome": None, "modification_log": []}

    fkey, primary_sid, decision = _pick_primary(active)
    if fkey is None:
        return empty

    spec = CLASSIC_FORMULAS[fkey]
    log.append(decision)
    log.append(f"立法：{spec['principle']}；选用 {spec['source']} 为底方")
    for r in spec.get("removed", []):
        log.append(f"化裁：{r}")

    ingredients: List[dict] = []
    substitutions: List[dict] = []
    used: Set[str] = set()

    def _append(item: dict, dose: float, role: str, purpose: str,
                matched: List[str]) -> None:
        used.add(item["name"])
        ingredients.append({
            "name": item["name"], "display": _display_name(item),
            "grams": dose, "role": role, "purpose": purpose,
            "nature": item["nature"], "functions": item["functions"],
            "matched_tags": matched, "cautions": item.get("cautions", []),
            "modern": item.get("modern", ""), "status": item["status"],
        })

    # ---- ② 底方骨架（保持不变的部分；单味被门禁/禁忌拦下时如实留痕，不强补）----
    for slot in spec["base"]:
        item = _resolve(slot["name"], active, used, log, "底方")
        if item is None:
            continue
        _append(item, slot["dose"], slot["role"], slot["note"],
                [primary_sid] if primary_sid else sorted(active & METABOLIC_TRIGGERS))

    # 保和丸底方的性别使药（女用玫瑰花疏肝、余用甘草调和）
    envoy = spec.get("sex_envoy")
    if envoy and len(ingredients) < MAX_TOTAL:
        name, dose, note = envoy.get(sex, envoy["default"])
        item = _resolve(name, active, used, log, "使药")
        if item is None and name != envoy["default"][0]:
            name2, dose2, note2 = envoy["default"]
            item = _resolve(name2, active, used, log, "使药")
            if item:
                name, dose, note = name2, dose2, note2
        if item:
            _append(item, dose, "使", note, [])

    # ---- ③ 兼证兼风险加减（主证自身不重复触发；外感忌补；条数与总量双封顶）----
    remaining = active - ({primary_sid} if primary_sid else set())
    adopted = 0
    forbid_tonic = bool(spec.get("forbid_tonic"))
    for rule in ADDITION_RULES:
        if adopted >= MAX_ADDITIONS or len(ingredients) >= MAX_TOTAL:
            break
        if not (rule["when"] & remaining):
            continue
        need = rule.get("need", 1)
        got = 0
        for name, dose in rule["add"]:
            if got >= need or len(ingredients) >= MAX_TOTAL:
                break
            if forbid_tonic and name in TONIC_NAMES:
                log.append(f"外感忌补：主证为外感，加减候选「{name}」属补益壅滞之品，"
                           f"待表解后再议调补，本方不入")
                continue
            item = _resolve(name, active, used, log, "加味")
            if item is None:
                # 目录门禁触发时记录替换链（供前端"目录门禁·已替换"戳）
                raw = kb.get_ingredient(name)
                if raw is not None and not kb.in_catalog(name):
                    substitutions.append({
                        "original": name,
                        "reason": f"{name} 目录状态为「{raw['status']}」，非药食同源目录，"
                                  f"按合规基线自动跳过（{raw.get('modern', '')}）"})
                continue
            if substitutions and "replaced_by" not in substitutions[-1]:
                substitutions[-1]["replaced_by"] = item["name"]
            _append(item, dose, "加", rule["reason"].split("：", 1)[-1],
                    sorted(rule["when"] & remaining))
            got += 1
        if got:
            log.append(f"加味：{rule['reason']}")
            adopted += 1

    if not ingredients:
        return empty

    # ---- ④ 安全说明 / 动态煎服法 ----
    safety_notes: List[str] = []
    for ing in ingredients:
        for c in ing["cautions"]:
            note = f"{ing['display']}：{c}"
            if note not in safety_notes:
                safety_notes.append(note)

    adjustments = [r["text"] for r in ADJUSTMENT_RULES]
    names = {i["name"] for i in ingredients}
    if names & _AROMATIC:
        adjustments.insert(0, "含" + "、".join(sorted(names & _AROMATIC))
                           + "等芳香之品：须「后下」——先冲泡其余原料 10 分钟，"
                             "再放入并加盖焖 3–5 分钟，避免挥发油散失。")
    if "蜂蜜" in names:
        adjustments.insert(0, "蜂蜜待茶汤降至温热（约 60℃ 以下）再调入，"
                              "不与药材同煮；1 岁以下婴儿禁用蜂蜜。")
    if names & _COLD_HERBS:
        adjustments.insert(0, "含" + "、".join(sorted(names & _COLD_HERBS))
                           + "等苦寒之品：中病即止、连续饮用不超过 1–2 周；"
                             "脾胃虚寒、经期女性慎用。")

    return {
        "ingredients": ingredients,
        "substitutions": [s for s in substitutions if "replaced_by" in s],
        "catalog_check": kb.catalog_check([i["name"] for i in ingredients]),
        "safety_notes": safety_notes,
        "adjustments": adjustments,
        "brew": _brew(),
        "formula_name": spec["name"],
        "source": spec["source"],
        "treatment_principle": spec["principle"],
        "primary_syndrome": primary_sid,
        "modification_log": log,
    }


def _brew() -> dict:
    return {
        "water_ml": 1000,
        "steep": "沸水冲入保温壶密封焖泡 15 分钟后饮用，可续沸水复泡至味淡",
        "schedule": "建议三餐后温饮：早餐后约 300ml、午餐后约 400ml、晚餐后约 300ml",
        "rules": ["当日饮完，不隔夜", "餐后饮用可减少空腹刺激；饮后如有不适即停用并咨询医师"],
    }
