"""脱敏（De-identification）：摄取管线的第二道防线。

第一道防线是抽取提示词（schema 不含身份字段）；本模块对所有自由文本再做
正则清洗，防止 OCR/LLM 把 PII 带进 description、notes 等字段。
覆盖：姓名行、各类单号、医生/打印员署名、机构名、证件号、手机号。
"""
from __future__ import annotations

import re
from typing import List

from ..schemas import ExtractionResult

_PATTERNS: List[tuple] = [
    # 姓名行（仅匹配显式"姓名"键，避免误伤"患者空腹"等临床描述）
    (re.compile(r"(姓\s*名|患者姓名)\s*[:：]?\s*[\u4e00-\u9fa5·]{1,6}"), "姓名：[已脱敏]"),
    # 各类单号
    (re.compile(r"(门诊号|住院号|病案号|病床号|超声号|检查号|条码号|申请单号|标本号)"
                r"\s*[:：]?\s*[A-Za-z0-9-]{3,}"), r"\1：[已脱敏]"),
    # 医生/打印员署名
    (re.compile(r"(申请医[师生]|检查医[师生]|报告医[师生]|审核医[师生]|超声医[师生]|"
                r"诊断医[师生]|操作医[师生]|打印员|记录员)\s*[:：]?\s*[\u4e00-\u9fa5·]{1,5}"),
     r"\1：[已脱敏]"),
    # 机构名（具体后缀在前，泛化"医院"最后）
    (re.compile(r"[\u4e00-\u9fa5]{2,14}(人民医院|中心医院|附属医院|中医医院|中医院|"
                r"妇幼保健院|保健院|卫生院|社区卫生服务中心|体检中心|诊所|医院)"),
     "[机构已脱敏]"),
    # 18 位身份证（\b 对中日韩字符无效，用数字环视）
    (re.compile(r"(?<![0-9Xx])\d{17}[\dXx](?![0-9Xx])"), "[证件号已脱敏]"),
    # 11 位手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[电话已脱敏]"),
]


def scrub_text(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def contains_pii(text: str) -> bool:
    """检测文本是否命中任一 PII 模式（用于测试与审计）。"""
    return any(p.search(text or "") for p, _ in _PATTERNS)


def scrub_extraction(result: ExtractionResult) -> ExtractionResult:
    """就地清洗抽取结果的全部自由文本字段，并置 deidentified=True。"""
    for f in result.findings:
        f.description = scrub_text(f.description)
        f.flags = [scrub_text(x) for x in f.flags]
    result.impressions = [scrub_text(x) for x in result.impressions]
    if result.notes:
        result.notes = scrub_text(result.notes)
    for o in result.observations:
        if o.display:
            o.display = scrub_text(o.display)
        if o.value_text:
            o.value_text = scrub_text(o.value_text)
    result.deidentified = True
    return result
