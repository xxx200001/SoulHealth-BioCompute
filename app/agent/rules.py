"""风险识别规则引擎（阶段五扩展版：覆盖常见体检异常，换病种可用）。

输入：repository.snapshot() 档案快照
输出：风险标签列表，每条含 id / label / severity / evidence / note

severity: info（提示）| watch（关注）| high（建议就医评估）
规则保持保守表述：影像与推断类标签一律带"可能/需临床确认"；
所有诊断切点仅用于风险提示，确诊一律指向医生。

覆盖：BMI、超声脂肪肝、肝酶(ALT/AST/GGT)、NASH 组合推断、胰腺回声、
胰岛素抵抗组合、空腹血糖/HbA1c、血脂四项、尿酸(分性别)、血压、
血肌酐、血红蛋白、以及通用影像所见(结节/结石/囊肿/息肉/占位)。
"""
from __future__ import annotations

from typing import List, Optional

PANCREAS_FLAG_HINTS = ("略强", "增强", "欠均匀", "不均匀")

# 通用影像关键词 → (标签id, severity)；脂肪肝/胰腺已有专门规则故排除
IMAGING_KEYWORDS = [
    ("占位", "imaging_mass", "high"),
    ("结节", "imaging_nodule", "watch"),
    ("结石", "imaging_stone", "watch"),
    ("囊肿", "imaging_cyst", "watch"),
    ("息肉", "imaging_polyp", "watch"),
]


def _latest(snapshot: dict, code: str) -> Optional[dict]:
    return snapshot.get("observations_latest", {}).get(code)


def _first_of(snapshot: dict, codes: List[str]) -> Optional[dict]:
    for c in codes:
        obs = _latest(snapshot, c)
        if obs is not None:
            return obs
    return None


def _num(obs: Optional[dict]) -> Optional[float]:
    return obs.get("value_num") if obs else None


def _fmt(obs: dict) -> str:
    unit = obs.get("unit") or ""
    ref = ""
    if obs.get("ref_high") is not None:
        lo = obs.get("ref_low")
        ref = f"（参考 {lo if lo is not None else ''}–{obs['ref_high']}）"
    return f"{obs.get('display') or obs['code']} {obs.get('value_num')} {unit}{ref}"


def _bmi_degree(bmi: float) -> str:
    if bmi >= 35:
        return "重度"
    if bmi >= 30:
        return "中度"
    return "轻度"


def identify_risks(snapshot: dict) -> List[dict]:
    tags: List[dict] = []
    seen_ids: set = set()

    def add(tag: dict) -> None:
        if tag["id"] not in seen_ids:
            seen_ids.add(tag["id"])
            tags.append(tag)

    impressions = [i["text"] for i in snapshot.get("impressions", [])]
    findings = snapshot.get("findings", [])
    sex = (snapshot.get("patient") or {}).get("sex")

    # ---------------- 1) 肥胖 / 超重（BMI）
    bmi = _num(_latest(snapshot, "BMI"))
    if bmi is None:
        p = snapshot.get("patient", {})
        if p.get("height_cm") and p.get("weight_kg"):
            h = p["height_cm"] / 100.0
            bmi = round(p["weight_kg"] / (h * h), 2)
    obesity = False
    if bmi is not None and bmi >= 28:
        obesity = True
        add({"id": "obesity", "label": f"肥胖（{_bmi_degree(bmi)}，BMI {bmi}）",
             "severity": "watch",
             "evidence": [f"BMI = {bmi}（参考 18.5–23.9；≥28 为肥胖）"],
             "note": "以 3–6 个月减重 5%–10% 为阶段目标"})
    elif bmi is not None and bmi >= 24:
        add({"id": "overweight", "label": f"超重（BMI {bmi}）", "severity": "info",
             "evidence": [f"BMI = {bmi}（24–27.9 为超重）"], "note": ""})
    elif bmi is not None and bmi < 18.5:
        add({"id": "underweight", "label": f"体重偏低（BMI {bmi}）",
             "severity": "watch",
             "evidence": [f"BMI = {bmi}（参考 18.5–23.9；<18.5 为体重偏低）"],
             "note": "体重偏低的原因需医生鉴别；本系统不提供减重方向的建议"})

    # ---------------- 2) 超声脂肪肝
    fatty_liver = any("脂肪肝" in t for t in impressions)
    if fatty_liver:
        ev = [f"超声提示：{t}" for t in impressions if "脂肪肝" in t]
        liver_flags = [f for f in findings if f["organ"] == "肝脏" and f.get("flags")]
        ev += [f"肝脏所见异常描述：{'、'.join(f['flags'])}" for f in liver_flags]
        add({"id": "fatty_liver_us", "label": "超声提示脂肪肝",
             "severity": "watch", "evidence": ev,
             "note": "病因分型需结合病史（饮酒等）与化验"})

    # ---------------- 3) 肝酶升高（ALT / AST / GGT）
    alt = _latest(snapshot, "ALT")
    ast_ = _latest(snapshot, "AST")
    ggt = _latest(snapshot, "GGT")
    enzyme_ev: List[str] = []
    alt_high = ast_high = ggt_high = False
    if _num(alt) is not None:
        limit = alt.get("ref_high") or 40
        if _num(alt) > limit:
            alt_high = True
            fold = round(_num(alt) / limit, 1)
            enzyme_ev.append(f"ALT {_num(alt)} {alt.get('unit') or ''}"
                             f"（参考上限 {limit}，约为上限 {fold} 倍）")
    if _num(ast_) is not None:
        limit = ast_.get("ref_high") or 40
        if _num(ast_) > limit:
            ast_high = True
            enzyme_ev.append(f"AST {_num(ast_)} {ast_.get('unit') or ''}（参考上限 {limit}）")
    if _num(ggt) is not None:
        limit = ggt.get("ref_high") or 45
        if _num(ggt) > limit:
            ggt_high = True
            enzyme_ev.append(f"GGT {_num(ggt)} {ggt.get('unit') or ''}（参考上限 {limit}）")
    enzyme_any = alt_high or ast_high or ggt_high
    if enzyme_any:
        sev = "high" if (alt_high and _num(alt) > 2 * (alt.get("ref_high") or 40)) else "watch"
        add({"id": "liver_enzyme_elevated", "label": "肝酶升高（ALT/AST/GGT）",
             "severity": sev, "evidence": enzyme_ev,
             "note": "建议尽早就诊评估并于 4–12 周复查"})

    # ---------------- 4) 脂肪性肝炎可能（组合推断）
    if fatty_liver and enzyme_any:
        add({"id": "nash_possible", "label": "脂肪性肝炎可能（需临床确认）",
             "severity": "high",
             "evidence": ["超声脂肪肝 + 肝酶升高的组合"],
             "note": "属推断性判断，确认与分型请以医生诊疗为准"})

    # ---------------- 5) 胰腺脂肪沉积可能
    panc = [f for f in findings if f["organ"] == "胰腺"]
    panc_flags = [fl for f in panc for fl in f.get("flags", [])
                  if any(h in fl for h in PANCREAS_FLAG_HINTS)]
    if panc_flags and (obesity or fatty_liver):
        ev = [f"胰腺所见：{'、'.join(sorted(set(panc_flags)))}"]
        ev += [f"超声提示：{t}" for t in impressions if "胰腺" in t]
        add({"id": "pancreatic_steatosis_possible",
             "label": "胰腺脂肪沉积可能（需结合临床）", "severity": "watch",
             "evidence": ev, "note": "影像学提示，非确诊"})

    # ---------------- 6) 空腹血糖 / HbA1c
    glu = _first_of(snapshot, ["GLU", "FPG", "FBG"])
    hba1c = _first_of(snapshot, ["HBA1C", "HBA1C%"])
    glu_ev: List[str] = []
    glu_sev = None
    v = _num(glu)
    if v is not None:
        if v >= 7.0:
            glu_sev = "high"
            glu_ev.append(f"空腹血糖 {v} {glu.get('unit') or 'mmol/L'}"
                          "（≥7.0 达糖尿病切点范围，需医生复查确认）")
        elif v >= 6.1:
            glu_sev = glu_sev or "watch"
            glu_ev.append(f"空腹血糖 {v} {glu.get('unit') or 'mmol/L'}（6.1–6.9 属空腹血糖受损）")
    v = _num(hba1c)
    if v is not None:
        if v >= 6.5:
            glu_sev = "high"
            glu_ev.append(f"糖化血红蛋白 {v}%（≥6.5% 达糖尿病切点范围，需医生复查确认）")
        elif v >= 6.0:
            glu_sev = glu_sev or "watch"
            glu_ev.append(f"糖化血红蛋白 {v}%（6.0–6.4% 提示糖代谢异常风险）")
    if glu_sev:
        add({"id": "glucose_high", "label": "血糖升高", "severity": glu_sev,
             "evidence": glu_ev,
             "note": "糖尿病诊断需医生非同日复查确认；建议内分泌科就诊"})

    # ---------------- 7) 血脂四项
    lipid_ev: List[str] = []
    lipid_sev = None
    tg = _latest(snapshot, "TG")
    v = _num(tg)
    if v is not None and v >= (tg.get("ref_high") or 1.7) and v >= 2.3:
        lipid_sev = "high" if v >= 5.6 else "watch"
        extra = "；≥5.6 有急性胰腺炎风险，请尽快就医" if v >= 5.6 else ""
        lipid_ev.append(f"甘油三酯 {v} {tg.get('unit') or 'mmol/L'}（≥2.3 为升高{extra}）")
    tc = _first_of(snapshot, ["TC", "CHOL"])
    v = _num(tc)
    if v is not None and v >= 6.2:
        lipid_sev = lipid_sev or "watch"
        lipid_ev.append(f"总胆固醇 {v} {tc.get('unit') or 'mmol/L'}（≥6.2 为升高）")
    ldl = _first_of(snapshot, ["LDL", "LDL-C", "LDLC"])
    v = _num(ldl)
    if v is not None and v >= 4.1:
        lipid_sev = lipid_sev or "watch"
        lipid_ev.append(f"低密度脂蛋白胆固醇 {v} {ldl.get('unit') or 'mmol/L'}（≥4.1 为升高）")
    hdl = _first_of(snapshot, ["HDL", "HDL-C", "HDLC"])
    v = _num(hdl)
    if v is not None and v < 1.0:
        lipid_sev = lipid_sev or "watch"
        lipid_ev.append(f"高密度脂蛋白胆固醇 {v} {hdl.get('unit') or 'mmol/L'}（<1.0 为偏低）")
    if lipid_sev:
        add({"id": "dyslipidemia", "label": "血脂异常", "severity": lipid_sev,
             "evidence": lipid_ev,
             "note": "治疗目标因心血管风险分层而异，请以医生评估为准"})

    # ---------------- 8) 尿酸（分性别阈值）
    ua = _first_of(snapshot, ["UA", "URIC", "SUA"])
    v = _num(ua)
    if v is not None:
        limit = 360 if sex == "female" else 420
        if v > limit:
            add({"id": "hyperuricemia", "label": "高尿酸血症",
                 "severity": "high" if v > 540 else "watch",
                 "evidence": [f"血尿酸 {v} {ua.get('unit') or 'µmol/L'}"
                              f"（{'女' if sex == 'female' else '男'}性参考上限 {limit}）"],
                 "note": "限酒精与含糖饮料、多饮水；建议复查并咨询医生"})

    # ---------------- 9) 血压（支持 SBP/DBP 分列数值，也支持 BP=「152/98」文本）
    sbp, dbp = _latest(snapshot, "SBP"), _latest(snapshot, "DBP")
    sv, dv = _num(sbp), _num(dbp)
    if sv is None and dv is None:
        bp = _first_of(snapshot, ["BP", "血压"])
        if bp is not None:
            import re as _re
            m = _re.search(r"(\d{2,3})\s*/\s*(\d{2,3})",
                           str(bp.get("value_text") or bp.get("value_num") or ""))
            if m:
                sv, dv = float(m.group(1)), float(m.group(2))
    if (sv is not None and sv >= 140) or (dv is not None and dv >= 90):
        sev = "high" if ((sv or 0) >= 180 or (dv or 0) >= 110) else "watch"
        ev = []
        if sv is not None:
            ev.append(f"收缩压 {sv} mmHg（≥140 为偏高）")
        if dv is not None:
            ev.append(f"舒张压 {dv} mmHg（≥90 为偏高）")
        add({"id": "blood_pressure_high", "label": "血压偏高（单次测量）",
             "severity": sev, "evidence": ev,
             "note": "高血压诊断需非同日多次测量确认"})

    # ---------------- 9.5) 感染 / 炎症方向（白细胞、C 反应蛋白）
    wbc = _first_of(snapshot, ["WBC", "白细胞"])
    v = _num(wbc)
    if v is not None:
        hi = wbc.get("ref_high") or 9.5
        lo = wbc.get("ref_low") or 3.5
        if v > hi:
            add({"id": "wbc_high", "label": "白细胞计数升高",
                 "severity": "high" if v > 15 else "watch",
                 "evidence": [f"白细胞 {v} ×10⁹/L（参考上限 {hi}）"],
                 "note": "常见于感染、炎症或应激反应；是否需要抗感染治疗须由医生"
                         "结合症状、分类计数与其他检查判断"})
        elif v < lo:
            add({"id": "wbc_low", "label": "白细胞计数偏低",
                 "severity": "watch",
                 "evidence": [f"白细胞 {v} ×10⁹/L（参考下限 {lo}）"],
                 "note": "可见于病毒感染、药物影响、血液系统问题等，建议复查并就诊评估"})
    crp = _first_of(snapshot, ["CRP", "HS-CRP", "HSCRP", "C反应蛋白"])
    v = _num(crp)
    if v is not None:
        hi = crp.get("ref_high") or 10
        if v > hi:
            add({"id": "crp_high", "label": "C 反应蛋白升高（炎症活动提示）",
                 "severity": "high" if v > 50 else "watch",
                 "evidence": [f"CRP {v} mg/L（参考上限 {hi}）"],
                 "note": "急性期炎症蛋白，显著升高多提示细菌感染或较强炎症活动；"
                         "伴发热、局部红肿热痛请尽快就医"})

    # ---------------- 9.6) 尿素氮
    bun = _first_of(snapshot, ["BUN", "UREA", "尿素氮"])
    v = _num(bun)
    if v is not None:
        hi = bun.get("ref_high") or 7.5
        if v > hi:
            add({"id": "bun_high", "label": "尿素氮偏高",
                 "severity": "watch",
                 "evidence": [f"尿素氮 {v} mmol/L（参考上限 {hi}）"],
                 "note": "受肾功能、蛋白摄入、脱水等多因素影响，"
                         "常与肌酐一并解读；请结合肾功能全套由医生评估"})

    # ---------------- 10) 血肌酐
    cr = _first_of(snapshot, ["CR", "CREA", "SCR"])
    v = _num(cr)
    if v is not None:
        limit = cr.get("ref_high") or (84 if sex == "female" else 104)
        if v > limit:
            add({"id": "renal_flag", "label": "肾功能指标异常（需就医评估）",
                 "severity": "watch",
                 "evidence": [f"血肌酐 {v} {cr.get('unit') or 'µmol/L'}（参考上限 {limit}）"],
                 "note": "建议肾内科就诊，由医生结合 eGFR 等综合评估"})

    # ---------------- 11) 血红蛋白
    hgb = _first_of(snapshot, ["HGB", "HB"])
    v = _num(hgb)
    if v is not None:
        low = hgb.get("ref_low") or (115 if sex == "female" else 130)
        if v < low:
            add({"id": "anemia_low_hgb", "label": "血红蛋白偏低",
                 "severity": "info",
                 "evidence": [f"血红蛋白 {v} {hgb.get('unit') or 'g/L'}（参考下限 {low}）"],
                 "note": "贫血病因需医生评估，勿自行长期补铁"})

    # ---------------- 12) 通用影像所见（结节/结石/囊肿/息肉/占位）
    for kw, tag_id, sev in IMAGING_KEYWORDS:
        hit_imp = [t for t in impressions if kw in t]
        hit_find = [f"{f['organ']}：{'、'.join(fl for fl in f['flags'] if kw in fl)}"
                    for f in findings if any(kw in fl for fl in f.get("flags", []))]
        if hit_imp or hit_find:
            ev = [f"提示：{t}" for t in hit_imp] + [f"所见 {h}" for h in hit_find]
            label = {"imaging_mass": "影像提示占位性病变（请尽快专科就诊）"}.get(
                tag_id, f"影像提示{kw}（建议专科随访）")
            add({"id": tag_id, "label": label, "severity": sev, "evidence": ev,
                 "note": "影像描述的性质判断请以专科医生意见为准"})

    # ---------------- 13) 自述症状（主观信息，仅提示就医，不作病因判断）
    notes = [n["text"].strip() for n in snapshot.get("notes", []) if n.get("text")]
    if notes:
        preview = [n if len(n) <= 40 else n[:40] + "…" for n in notes[-3:]]
        add({"id": "symptom_note", "label": "存在自述症状（需结合临床）",
             "severity": "info",
             "evidence": [f"档案记录的自述内容：{s}" for s in preview],
             "note": "症状的病因判断需医生面诊，本系统不对症状作诊断性推断"})

    # ---------------- 14) 胰岛素抵抗风险（组合推断）
    combo = sum([obesity, fatty_liver, enzyme_any, bool(panc_flags),
                 "glucose_high" in seen_ids])
    if combo >= 3:
        add({"id": "insulin_resistance_risk",
             "label": "胰岛素抵抗风险较高（推断）", "severity": "watch",
             "evidence": ["肥胖 / 脂肪肝 / 肝酶升高 / 胰腺回声改变 / 血糖升高中"
                          "多项并存的组合"],
             "note": "可与医生讨论空腹胰岛素 / HOMA-IR 等评估"})

    return tags
