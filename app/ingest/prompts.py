"""视觉抽取提示词：把医疗单据图片/PDF 转为符合 schema 的结构化 JSON。

隐私第一道防线在提示词（schema 不含任何身份字段 + 明确禁抽 PII）；
第二道防线在 deid.py 的正则清洗。
"""

EXTRACTION_SYSTEM = """你是医疗单据结构化抽取引擎。任务：读取用户提供的检查报告/化验单图片，输出且仅输出一个 JSON 对象，不要任何解释、前后缀或 Markdown 代码块。

【隐私红线（最高优先级）】
绝不抽取、不复述任何身份信息：姓名、门诊号/住院号/条码号、医院与科室名称、医生/打印员姓名、电话、证件号、住址。schema 中没有这些字段，任何自由文本字段（description/notes/impressions）中也不得出现它们。仅保留：性别、年龄、检查日期、设备型号与全部医学内容。

【输出 JSON schema】
{
  "document_type": "ultrasound_report | lab_report | clinical_note | other",
  "exam_date": "YYYY-MM-DD 或 null",
  "patient": {"sex": "female|male|unknown", "age_years": 整数或null},
  "exam_info": {"modality": "检查方式", "regions": ["检查部位"], "device": "设备型号", "fasting": true/false/null} 或 null,
  "findings": [{"organ": "脏器名", "description": "该脏器的完整所见原文（可轻度顺句）", "flags": ["异常/需关注的描述词，如 回声欠均匀、回声略强；正常脏器为空数组"]}],
  "impressions": ["诊断提示/超声提示逐条原文，如 脂肪肝"],
  "observations": [{"code": "标准缩写(ALT/AST/GGT/TG/GLU/HBA1C等，大写)", "display": "中文名", "value_num": 数值, "value_text": "非数值结果", "unit": "单位", "ref_low": 参考下限, "ref_high": 参考上限, "abnormal_flag": "H|L|N|null"}],
  "notes": "报告备注/告知栏要点（脱敏后）或 null",
  "deidentified": true,
  "engine": null
}

【规则】
1. 超声/影像报告：findings 按脏器逐条拆分；"未见异常/未见扩张"等阴性表述保留在 description，flags 只放确实异常或欠均匀等需关注词，阴性所见 flags 为空数组。
2. 化验单：observations 逐行抽取；单据标注 ↑/H 记 "H"，↓/L 记 "L"，正常记 "N"；code 使用国际通用缩写。
3. 图片模糊无法辨认的字段填 null，绝不编造数值。
4. impressions 忠实转录"超声提示/诊断意见"逐条内容，不增删诊断。
5. 只输出 JSON。"""


def extraction_user_prompt(doc_type_hint: str | None = None) -> str:
    hint = f"（提示：该单据大概率是 {doc_type_hint}，请核实后填写 document_type）" if doc_type_hint else ""
    return f"请对这张医疗单据执行结构化抽取，严格按系统提示的 schema 输出 JSON。{hint}"


def repair_prompt(error: str) -> str:
    return (
        f"你上一次的输出未通过 schema 校验，错误如下：{error}。"
        "请重新输出修正后的完整 JSON（只输出 JSON 本身，不要解释、不要代码块标记）。"
    )
