"""《药食同源代茶饮个性化建议》生成器（阶段五：随风险标签泛化）。

章节结构与业务样例文档同构，但辨证参考、调理靶点、观察节点、饮食要点
均按本次风险标签条件生成：肝脂患者与糖脂/尿酸患者得到不同的文案与
复查建议；配伍寒热制衡说明由实际入方原料的性味动态组装，不再硬编码
具体药名。全部内容结构化数据驱动，输出前经 compliance.assert_clean。
"""
from __future__ import annotations

from typing import List, Set

LIVER_TAGS = {"fatty_liver_us", "nash_possible", "liver_enzyme_elevated",
              "pancreatic_steatosis_possible"}
IMAGING_TAGS = {"imaging_nodule", "imaging_stone", "imaging_cyst",
                "imaging_polyp", "imaging_mass"}


def _patient_line(snapshot: dict, risk_tags: List[dict]) -> str:
    p = snapshot["patient"]
    sex = {"female": "女", "male": "男"}.get(p.get("sex"), "性别未录")
    parts = [f"{p.get('age_years', '—')} 岁{sex}性"]
    if p.get("height_cm") and p.get("weight_kg"):
        parts.append(f"身高 {p['height_cm']:g}cm、体重 {p['weight_kg']:g}kg")
    bmi = snapshot.get("observations_latest", {}).get("BMI")
    if bmi:
        parts.append(f"BMI {bmi['value_num']}")
    labels = [t["label"] for t in risk_tags]
    if labels:
        parts.append("风险提示：" + "；".join(labels))
    return "，".join(parts) + "。"


def _syndrome_line(syndrome_tags) -> str:
    if not syndrome_tags:
        return ""
    labels = "、".join(s["label"] for s in syndrome_tags)
    return (f"自述症状识别到与「{labels}」相关的关键词，已在下方组方中纳入对应"
            f"食养方向；**此为自述关键词匹配，不构成中医辨证或西医诊断**，"
            f"具体证型请以中医师面诊四诊合参为准。")


def _syndrome_bullets(tags: Set[str]) -> List[str]:
    out: List[str] = []
    if "insomnia_pattern" in tags:
        out.append("自述失眠/睡眠不佳者，食养可循「养心安神、清心除烦」思路；"
                   "证型判断（心脾两虚/阴虚火旺等具体分型）仍需中医师面诊核实。")
    if "throat_pattern" in tags:
        out.append("自述咽喉不适者，食养可循「利咽清热、疏风」思路；"
                   "若伴发热、吞咽困难或持续超过一周不缓解，请及时就医排查咽喉炎症或其他病因。")
    if "qi_deficiency_pattern" in tags:
        out.append("自述乏力气短者，食养可循「补气健脾」思路；"
                   "长期不明原因乏力建议就诊排查贫血、甲状腺功能等器质性原因后再谈食养。")
    if "damp_heat_pattern" in tags:
        out.append("自述口苦苔腻、小便黄等湿热倾向者，食养循「清热利湿」思路，"
                   "苦寒之品中病即止（连续不超过 1–2 周）；脾胃虚寒者不宜此方向。")
    if "yin_deficiency_pattern" in tags:
        out.append("自述口干咽燥、手足心热等阴虚倾向者，食养循「甘寒养阴生津」思路；"
                   "长期盗汗、消瘦须就诊排查甲亢、结核、糖尿病等器质性原因。")
    if "constipation_pattern" in tags:
        out.append("自述大便干结者，食养循「润肠通便」思路（润下不峻下）；"
                   "便秘伴便血、消瘦、腹痛或排便习惯突然改变，请务必就医排查。")
    if "wind_heat_pattern" in tags:
        out.append("自述咽痛发热等风热外感初起表现者，食养仅作辅助；"
                   "外感期间饮食清淡，高热不退、症状加重请及时就医并暂停本方。")
    if "spleen_damp_pattern" in tags:
        out.append("自述便溏、食少腹胀等脾虚湿盛倾向者，食养循「健脾化湿固涩」思路，"
                   "本方向不使用任何润肠滑利之品；腹泻持续或伴消瘦请就医。")
    if "cough_phlegm_pattern" in tags:
        out.append("自述咳嗽有痰者，食养循「宣肺化痰」思路（仅用甜杏仁）；"
                   "咳嗽超过 2 周、痰中带血、伴发热胸痛者请务必就医。")
    if "summer_damp_pattern" in tags:
        out.append("自述暑天头身困重、食欲差者，食养循「芳香化浊解暑」思路；"
                   "高温环境下大量出汗须先保证水盐补充，中暑先兆请立即降温就医。")
    if tags & (LIVER_TAGS | {"obesity", "overweight"}):
        out.append("形体肥胖或肝胰声像改变者，多与脾失健运、肝失疏泄、湿浊膏脂内停相关，"
                   "可循「健脾化湿、疏肝化浊、行气和中」思路食养。")
    if "glucose_high" in tags:
        out.append("血糖偏高者常见「脾瘅」之象（过食肥甘、中满内热），食养以清化湿热、"
                   "生津助运为参考方向。")
    if "dyslipidemia" in tags:
        out.append("血脂异常多归于「痰浊膏脂」范畴，食养以消积化浊、健脾祛湿为参考方向。")
    if "hyperuricemia" in tags:
        out.append("血尿酸偏高者多与湿浊内蕴相关，食养以利湿泄浊为参考方向，"
                   "并须配合限酒与饮水管理。")
    if "blood_pressure_high" in tags:
        out.append("血压偏高者可参考平肝、清降思路，但血压管理以监测与就医为主，"
                   "食养仅为辅助。")
    if not out:
        out.append("证型判断需中医师面诊四诊合参，以下配伍仅为依据档案风险方向拟定的"
                   "食养参考思路。")
    return out


def _target_bullets(tags: Set[str]) -> List[str]:
    out: List[str] = []
    if "insomnia_pattern" in tags:
        out.append("睡眠方向：养心安神之品配合规律作息（固定入睡/起床时间、"
                   "睡前减少屏幕使用）；症状持续 2 周以上建议就诊评估。")
    if "throat_pattern" in tags:
        out.append("咽喉方向：利咽清热之品配合充分饮水、避免辛辣刺激；"
                   "声音嘶哑超过 2 周或进行性加重请尽快五官科就诊。")
    if "qi_deficiency_pattern" in tags:
        out.append("体力方向：补气健脾之品配合规律作息、避免过度劳累；"
                   "若伴明显体重下降、发热等请及时就医。")
    if "damp_heat_pattern" in tags:
        out.append("湿热方向：清利之品配合清淡饮食、忌酒与油炸辛辣；"
                   "观察舌苔与二便变化，1–2 周无改善请就诊。")
    if "yin_deficiency_pattern" in tags:
        out.append("津液方向：养阴之品配合充足饮水、避免熬夜与辛辣燥热饮食。")
    if "constipation_pattern" in tags:
        out.append("肠道方向：润下之品配合足量饮水（每日 1500ml 以上）、"
                   "增加膳食纤维与规律排便习惯训练。")
    if "spleen_damp_pattern" in tags:
        out.append("脾胃方向：健脾之品配合规律三餐、细嚼慢咽、忌生冷油腻。")
    if "cough_phlegm_pattern" in tags:
        out.append("呼吸方向：化痰之品配合充分休息、远离烟雾刺激；"
                   "记录咳嗽时段与痰的性状供医生参考。")
    if tags & {"obesity", "overweight"}:
        out.append("体重管理方向：健脾化湿、消积化浊，配合饮食调整以利体重下降。")
    if tags & LIVER_TAGS:
        out.append("肝胰养护方向：疏肝理气、清化湿浊，配合生活方式干预与定期复查，"
                   "关注肝酶与肝胰声像的变化趋势。")
    if "glucose_high" in tags:
        out.append("糖代谢方向：控制添加糖与精制碳水是核心，茶饮中生津助运之品仅为辅助；"
                   "血糖复查确认请以医生安排为准。")
    if "dyslipidemia" in tags:
        out.append("血脂方向：消积化浊之品配合低油饮食，以复查血脂四项的趋势为准。")
    if "hyperuricemia" in tags:
        out.append("尿酸方向：利湿泄浊之品配合限酒、限含糖饮料与充足饮水，观察血尿酸复查值。")
    if "blood_pressure_high" in tags:
        out.append("血压方向：平肝清降之品为辅助，核心是限盐、家庭血压监测与按需就医。")
    if not out:
        out.append("一般养护方向：健脾和中，配合规律作息与均衡饮食。")
    return out


def _balance_sentence(ings: List[dict]) -> str:
    cool = [i["display"] for i in ings if ("寒" in i["nature"] or "凉" in i["nature"])]
    warm = [i["display"] for i in ings if "温" in i["nature"]]
    if cool and warm:
        core = (f"全方寒热制衡：偏凉之{('、'.join(cool[:3]))}与偏温之"
                f"{('、'.join(warm[:3]))}相配，佐以平性之品，整体较为温和。")
    elif cool:
        core = (f"全方以平、凉为主（{('、'.join(cool[:3]))}等），脾胃虚寒者请减量并"
                "观察耐受。")
    elif warm:
        core = f"全方以平、温为主（{('、'.join(warm[:3]))}等），性味整体温和。"
    else:
        core = "全方以平性之品为主，性味整体温和。"
    return (core + "任何食材均存在个体差异，不作「无不良反应」承诺；"
                   "下列人群请按第六节微调或先咨询医师。")


def _observe_bullets(tags: Set[str]) -> List[str]:
    out = ["第 1–2 周：可关注体感变化（餐后困倦、排便、浮肿等）并记录体重、腰围；"
           "初期体重波动多与水分相关，不作为减脂依据。"]
    if "insomnia_pattern" in tags:
        out.append("睡眠：建议记录入睡时间与夜间觉醒次数（可用手机备忘录），"
                   "2 周无改善或加重请就诊，排查焦虑、甲状腺功能异常等其他原因。")
    if "throat_pattern" in tags:
        out.append("咽喉：观察症状是否随饮水、休息缓解；若反复发作建议排查过敏、"
                   "反流性咽喉炎等常见病因。")
    if "qi_deficiency_pattern" in tags:
        out.append("体力：记录乏力发作的时间规律（如晨起明显还是午后加重），"
                   "供医生判断方向。")
    if tags & LIVER_TAGS:
        out.append("第 4–8 周：建议复查肝功能（ALT、AST、GGT 等），观察指标变化趋势。")
    if "glucose_high" in tags:
        out.append("血糖：请按医生安排尽快复查空腹血糖 / 糖化血红蛋白（或 OGTT）以"
                   "确认诊断，勿以茶饮观察替代复查。")
    if "dyslipidemia" in tags:
        out.append("第 8–12 周：复查血脂四项，与本次结果对照趋势。")
    if "hyperuricemia" in tags:
        out.append("第 2–4 周：复查血尿酸，同时记录饮酒与含糖饮料摄入情况供医生参考。")
    if "blood_pressure_high" in tags:
        out.append("血压：连续 7 天早晚各测一次并记录，就诊时供医生判断是否为高血压。")
    if (tags & LIVER_TAGS) or (tags & IMAGING_TAGS):
        out.append("影像：可与医生商议在 8–12 周复查腹部超声或对应部位影像，"
                   "评估声像变化；后续方案据复查结果与医生意见调整。")
    return out


def _diet_bullets(tags: Set[str]) -> List[str]:
    out = ["重点规避：含糖饮料、奶茶、蛋糕甜品、果汁等添加糖食品，以及深夜夜宵——"
           "对体重、血糖与脂肪肝的影响最直接。",
           "主食适量并做粗细搭配；蛋白质（鱼禽蛋豆）与蔬菜保证充足。"]
    if "insomnia_pattern" in tags:
        out.append("睡眠：午后避免浓茶、咖啡等含咖啡因饮品；晚餐不过饱、不过晚。")
    if "qi_deficiency_pattern" in tags:
        out.append("体力：避免长期节食或过度运动消耗，保证优质蛋白与铁、维生素 B 族摄入。")
    if tags & (LIVER_TAGS | {"dyslipidemia", "obesity"}):
        out.append("油脂适量：减少油炸与肥肉，家常烹调即可；茶饮不能抵消饮食本身的影响。")
    if "hyperuricemia" in tags:
        out.append("尿酸管理：限制酒精（尤其啤酒）与动物内脏、浓肉汤；心肾功能正常者"
                   "每日饮水约 2000ml。")
    if "blood_pressure_high" in tags:
        out.append("限盐：每日食盐控制在 5g 以内，警惕酱料、腌制品等隐形盐。")
    return out


def build_blocks(ctx: dict) -> List[tuple]:
    snapshot, risk_tags = ctx["snapshot"], ctx["risk_tags"]
    syndrome_tags = ctx.get("syndrome_tags") or []
    formula = ctx["formula"]
    ings = formula["ingredients"]
    brew = formula["brew"]
    tag_ids = {t["id"] for t in risk_tags} | {s["id"] for s in syndrome_tags}

    blocks: List[tuple] = [
        ("title", "药食同源代茶饮 · 个性化建议（合规演示版）"),
        ("p", [("适配对象：", True), (_patient_line(snapshot, risk_tags), False)]),
        ("note", "本建议由 SOULHEALTH Demo 基于健康档案自动生成，定位为健康管理"
                 "辅助与食养参考，不替代医生诊断与治疗；配方与用量建议经中医师/"
                 "营养师面诊核实后使用。"),

        ("h1", "核心前提说明"),
        ("bullet", "合规性：本方所选原料均通过系统的《药食同源目录》校验（校验明细见文末附表）；"
                   "目录外原料已按合规基线自动替换并留痕。"),
        ("bullet", "调理思路：茶饮是辅助手段，核心仍是饮食调整、作息与随访复查；"
                   "本方按本次识别的风险方向个性化选材。"),
        ("bullet", "预期管理：起效节奏与幅度因人而异，请以复查结果为准；本方不设任何"
                   "时间或数字承诺。"),

        ("h1", "一、辨证参考与调理靶点"),
        ("h2", "患者基础情况"),
        ("p", _patient_line(snapshot, risk_tags)),
        ("h2", "中医辨证参考（供中医师面诊核实）"),
    ]
    syn_line = _syndrome_line(syndrome_tags)
    if syn_line:
        blocks.append(("note", syn_line))
    for s in _syndrome_bullets(tag_ids):
        blocks.append(("bullet", s))
    blocks.append(("h2", "调理靶点（辅助方向）"))
    for s in _target_bullets(tag_ids):
        blocks.append(("bullet", s))

    blocks += [
        ("h1", "二、专属单日配方"),
    ]
    if formula.get("formula_name"):
        blocks += [
            ("p", [("本方：", True),
                   (f"{formula['formula_name']}（{formula['source']}）", False)]),
            ("p", [("治则（立法）：", True),
                   (formula["treatment_principle"] or "", False)]),
            ("note", "开方路径遵循「辨证 → 立法 → 底方 → 加减」：先按主证选定经典底方"
                     "（骨架不变），再按兼证与兼夹风险加减，每一步依据见下方"
                     "「加减与化裁依据」；证型来自自述关键词匹配，不构成中医辨证，"
                     "具体证型与处方请以中医师四诊合参为准。"),
        ]
    blocks += [
        ("p", [("单日剂量：", True),
               ("、".join(f"{i['display']}{i['grams']}g" for i in ings), False)]),
        ("table", {
            "header": ["原料", "用量", "角色", "性味", "本方要点"],
            "rows": [[i["display"], f"{i['grams']}g", i["role"], i["nature"],
                      i["purpose"]] for i in ings],
        }),
    ]

    for sub in formula["substitutions"]:
        blocks.append(("note",
                       f"目录校验替换：{sub['reason']}；本方已以目录内的"
                       f"「{sub['replaced_by']}」承接同类功用。"))

    if formula.get("modification_log"):
        blocks.append(("h2", "加减与化裁依据（逐条留痕）"))
        for entry in formula["modification_log"]:
            blocks.append(("bullet", entry))

    blocks.append(("h2", "各原料功用简析"))
    for idx, i in enumerate(ings, 1):
        blocks.append(("p", [(f"{idx}. {i['display']} {i['grams']}g（{i['role']}）　", True),
                             (f"性味{i['nature']}；传统功用：{'、'.join(i['functions'])}。"
                              f"{i['modern']}。本方取其「{i['purpose']}」之用。", False)]))

    blocks += [
        ("h2", "配伍与安全说明"),
        ("p", _balance_sentence(ings)),
        ("h1", "三、标准化冲泡与饮用"),
        ("bullet", f"冲泡：食材快速清洗去浮尘，置保温壶，{brew['water_ml']}ml {brew['steep']}。"),
        ("bullet", f"饮用：{brew['schedule']}。"),
    ]
    for rule in brew["rules"]:
        blocks.append(("bullet", rule))
    blocks.append(("bullet", "重油聚餐后可续水再泡、加饮约 200ml 温茶汤，"
                             "有助缓解餐后油腻不适感（不改变总体饮食建议）。"))

    blocks.append(("h1", "四、观察节点与复查随访建议（以复查为准，因人而异）"))
    for s in _observe_bullets(tag_ids):
        blocks.append(("bullet", s))
    blocks.append(("p", "如任一阶段出现指标继续上升、明显乏力、持续不适等情况，"
                        "请及时就诊，不必等待观察周期结束。"))

    blocks.append(("h1", "五、饮食执行要点"))
    for s in _diet_bullets(tag_ids):
        blocks.append(("bullet", s))

    blocks.append(("h1", "六、安全禁忌与个性化微调"))
    blocks.append(("bullet", "孕期停用本方。"))
    for adj in formula["adjustments"]:
        if "孕期" in adj and any(b[0] == "bullet" and "孕期停用" in b[1] for b in blocks):
            continue
        blocks.append(("bullet", adj))
    for note in formula["safety_notes"]:
        blocks.append(("bullet", note))

    blocks += [
        ("h1", "七、生活方式配合（建议而非强制）"),
        ("p", "每日快走或散步 10–30 分钟即有助于代谢改善，可从 10 分钟起步、循序渐进；"
              "保证睡眠、避免熬夜同样重要。茶饮 + 饮食调整 + 轻量活动 + 定期复查，"
              "四者配合的整体效果通常优于任何单一手段。"),

        ("h1", "附：药食同源目录校验结果"),
        ("table", {
            "header": ["原料", "目录状态", "校验结论"],
            "rows": [[c["name"], c["status"], "通过" if c["ok"] else f"未通过（{c['note']}）"]
                     for c in formula["catalog_check"]],
        }),
    ]

    high = [t["label"] for t in risk_tags if t["severity"] == "high"]
    if high:
        blocks.append(("note",
                       f"就医提示：本次存在建议就医评估的条目（{'；'.join(high)}），"
                       "请尽早至相应专科就诊，并按医嘱复查随访；本茶饮为食养辅助，"
                       "不替代诊疗。"))
    else:
        blocks.append(("note", "建议定期体检、按医嘱随访；本茶饮为食养辅助，不替代诊疗。"))

    return blocks
