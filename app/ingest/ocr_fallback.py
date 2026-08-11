"""离线 OCR 兜底引擎：PaddleOCR 出文本 + 规则抽取。

用途：私有化/无外网环境，或视觉 LLM 不可用时的降级路径。
规则抽取按中文超声报告的典型版式设计（脏器分段 + "超声提示"栏），
带否定词处理（"未见扩张"不会被标为异常 flag）。
化验单的规则抽取表格版式差异大，此处只做基础行解析；
生产建议用 PP-StructureV3 表格识别后再映射（见 README 路线图）。
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..schemas import ExamInfo, ExtractionResult, Finding, PatientBasics

ABNORMAL_HINTS = [
    "欠均匀", "不均匀", "略强", "增强", "增粗", "增大", "增厚", "扩张", "占位",
    "结石", "结节", "囊肿", "脂肪肝", "浸润", "毛糙", "钙化", "偏高", "升高",
]
NEGATIONS = ["未见", "未再", "无明显", "不伴", "不", "无"]

_ORGAN_KEYS = [  # (匹配关键词, 归一化脏器名)
    ("胰腺", "胰腺"), ("胆囊", "胆囊"), ("脾脏", "脾脏"),
    ("肾脏", "双肾"), ("双肾", "双肾"), ("肝脏", "肝脏"),
]
_IMPRESSION_HEAD = re.compile(r"(超声提示|超声诊断|诊断意见|检查提示|印象)\s*[:：]?")
_IMPRESSION_STOP = re.compile(r"(告知|检查医|报告医|审核|打印员|诊断时间)")
_DATE_RE = re.compile(r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})")
_AGE_RE = re.compile(r"(\d{1,3})\s*岁")


def ocr_text(image_path: str) -> str:
    """调用 PaddleOCR 识别整图文本；未安装时给出可执行的安装指引。"""
    try:
        from paddleocr import PaddleOCR  # 惰性导入
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "未安装 paddleocr。离线 OCR 兜底需要：\n"
            "  pip install paddlepaddle paddleocr\n"
            "或改用默认引擎 SOULHEALTH_OCR_ENGINE=vision_llm。"
        ) from exc
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    result = ocr.ocr(str(image_path), cls=True)
    lines: List[str] = []
    for page in result or []:
        for item in page or []:
            try:
                lines.append(item[1][0])
            except (IndexError, TypeError):
                continue
    return "\n".join(lines)


def _flags_in(sentence: str) -> List[str]:
    """提取异常提示词，带否定处理：命中词前 6 字内若出现否定词则跳过。"""
    flags: List[str] = []
    for hint in ABNORMAL_HINTS:
        idx = sentence.find(hint)
        while idx != -1:
            window = sentence[max(0, idx - 6):idx]
            if not any(neg in window for neg in NEGATIONS):
                if hint not in flags:
                    flags.append(hint)
                break
            idx = sentence.find(hint, idx + 1)
    return flags


def _parse_impressions(lines: List[str]) -> List[str]:
    impressions: List[str] = []
    in_section = False
    for line in lines:
        if not in_section:
            if _IMPRESSION_HEAD.search(line):
                in_section = True
                tail = _IMPRESSION_HEAD.split(line)[-1].strip()
                if tail:
                    impressions.append(tail)
            continue
        if _IMPRESSION_STOP.search(line):
            break
        cleaned = re.sub(r"^\s*\d+\s*[.、．]?\s*", "", line).strip()
        if cleaned:
            impressions.append(cleaned)
    return impressions


def extract_with_rules(text: str, doc_type_hint: Optional[str] = None) -> ExtractionResult:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    full = "\n".join(lines)

    m = _DATE_RE.search(full)
    exam_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

    sex = "female" if re.search(r"[性别]\s*[:：]?\s*女|别\s*[:：]\s*女", full) else (
        "male" if re.search(r"[性别]\s*[:：]?\s*男|别\s*[:：]\s*男", full) else "unknown")
    m_age = _AGE_RE.search(full)
    age = int(m_age.group(1)) if m_age else None

    findings: List[Finding] = []
    seen = set()
    sentences = re.split(r"[。\n]", full)
    for key, organ in _ORGAN_KEYS:
        if organ in seen:
            continue
        matched = [s.strip() for s in sentences if key in s and s.strip()]
        if matched:
            desc = "。".join(matched) + "。"
            findings.append(Finding(organ=organ, description=desc,
                                    flags=_flags_in(desc)))
            seen.add(organ)

    impressions = _parse_impressions(lines)
    doc_type = doc_type_hint or ("ultrasound_report" if (findings or impressions) else "other")

    return ExtractionResult(
        document_type=doc_type,
        exam_date=exam_date,
        patient=PatientBasics(sex=sex, age_years=age),
        exam_info=ExamInfo(modality="超声" if doc_type == "ultrasound_report" else None),
        findings=findings,
        impressions=impressions,
        observations=[],
        notes="由离线 OCR + 规则引擎抽取，字段完整度低于视觉 LLM 路径。",
        deidentified=False,  # 由 pipeline 统一走 deid
        engine="paddleocr_rules",
    )


def extract_from_image(image_path: str,
                       doc_type_hint: Optional[str] = None) -> ExtractionResult:
    return extract_with_rules(ocr_text(image_path), doc_type_hint)
