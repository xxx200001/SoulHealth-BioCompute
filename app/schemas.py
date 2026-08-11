"""结构化抽取结果的数据模型与校验。

仅依赖标准库（dataclasses），保证核心管线在无任何三方依赖的环境也能运行/测试。
from_dict() 为严格模式：汇总所有问题后抛 ValueError —— 该错误信息会被回喂给
视觉 LLM 做一次自修正重试（见 ingest/vision_llm.py）。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

DOCUMENT_TYPES = {"ultrasound_report", "lab_report", "clinical_note", "other"}
SEXES = {"female", "male", "unknown"}
ABNORMAL_FLAGS = {"H", "L", "N"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class PatientBasics:
    sex: str = "unknown"
    age_years: Optional[int] = None


@dataclass
class ExamInfo:
    modality: Optional[str] = None
    regions: List[str] = field(default_factory=list)
    device: Optional[str] = None
    fasting: Optional[bool] = None


@dataclass
class Finding:
    organ: str = ""
    description: str = ""
    flags: List[str] = field(default_factory=list)


@dataclass
class Observation:
    code: str = ""
    display: Optional[str] = None
    value_num: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    abnormal_flag: Optional[str] = None


@dataclass
class ExtractionResult:
    document_type: str = "other"
    exam_date: Optional[str] = None
    patient: PatientBasics = field(default_factory=PatientBasics)
    exam_info: Optional[ExamInfo] = None
    findings: List[Finding] = field(default_factory=list)
    impressions: List[str] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)
    notes: Optional[str] = None
    deidentified: bool = False
    engine: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 严格解析

def _opt_float(value, path: str, errors: List[str]) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{path} 应为数值，实际为 {value!r}")
        return None


def _opt_int(value, path: str, errors: List[str]) -> Optional[int]:
    f = _opt_float(value, path, errors)
    return int(f) if f is not None else None


def _str_list(value, path: str, errors: List[str]) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        errors.append(f"{path} 应为字符串数组")
        return []
    return [str(x).strip() for x in value if str(x).strip()]


def from_dict(data: dict) -> ExtractionResult:
    """严格校验并构造 ExtractionResult；问题汇总后一次性抛出。"""
    if not isinstance(data, dict):
        raise ValueError("抽取结果必须是 JSON 对象（dict）")
    errors: List[str] = []

    doc_type = str(data.get("document_type", "")).strip()
    if doc_type not in DOCUMENT_TYPES:
        errors.append(
            f"document_type 非法: {doc_type!r}，应为 {sorted(DOCUMENT_TYPES)} 之一"
        )
        doc_type = "other"

    exam_date = data.get("exam_date") or None
    if exam_date is not None:
        exam_date = str(exam_date).strip()
        if not _DATE_RE.match(exam_date):
            errors.append(f"exam_date 应为 YYYY-MM-DD，实际为 {exam_date!r}")
            exam_date = None

    p_raw = data.get("patient") or {}
    sex = str(p_raw.get("sex", "unknown") or "unknown").strip().lower()
    if sex not in SEXES:
        errors.append(f"patient.sex 非法: {sex!r}，应为 {sorted(SEXES)}")
        sex = "unknown"
    patient = PatientBasics(
        sex=sex, age_years=_opt_int(p_raw.get("age_years"), "patient.age_years", errors)
    )

    exam_info = None
    e_raw = data.get("exam_info")
    if isinstance(e_raw, dict):
        fasting = e_raw.get("fasting")
        if fasting is not None and not isinstance(fasting, bool):
            errors.append("exam_info.fasting 应为布尔值")
            fasting = None
        exam_info = ExamInfo(
            modality=(str(e_raw["modality"]).strip() if e_raw.get("modality") else None),
            regions=_str_list(e_raw.get("regions"), "exam_info.regions", errors),
            device=(str(e_raw["device"]).strip() if e_raw.get("device") else None),
            fasting=fasting,
        )

    findings: List[Finding] = []
    for i, f_raw in enumerate(data.get("findings") or []):
        if not isinstance(f_raw, dict):
            errors.append(f"findings[{i}] 应为对象")
            continue
        organ = str(f_raw.get("organ", "")).strip()
        desc = str(f_raw.get("description", "")).strip()
        if not organ or not desc:
            errors.append(f"findings[{i}] 缺少 organ 或 description")
            continue
        findings.append(
            Finding(organ=organ, description=desc,
                    flags=_str_list(f_raw.get("flags"), f"findings[{i}].flags", errors))
        )

    observations: List[Observation] = []
    for i, o_raw in enumerate(data.get("observations") or []):
        if not isinstance(o_raw, dict):
            errors.append(f"observations[{i}] 应为对象")
            continue
        code = str(o_raw.get("code", "")).strip().upper()
        if not code:
            errors.append(f"observations[{i}] 缺少 code（标准化指标缩写，如 ALT/GGT）")
            continue
        flag = o_raw.get("abnormal_flag")
        if flag is not None:
            flag = str(flag).strip().upper() or None
            if flag is not None and flag not in ABNORMAL_FLAGS:
                errors.append(f"observations[{i}].abnormal_flag 应为 H/L/N")
                flag = None
        obs = Observation(
            code=code,
            display=(str(o_raw["display"]).strip() if o_raw.get("display") else None),
            value_num=_opt_float(o_raw.get("value_num"), f"observations[{i}].value_num", errors),
            value_text=(str(o_raw["value_text"]).strip() if o_raw.get("value_text") else None),
            unit=(str(o_raw["unit"]).strip() if o_raw.get("unit") else None),
            ref_low=_opt_float(o_raw.get("ref_low"), f"observations[{i}].ref_low", errors),
            ref_high=_opt_float(o_raw.get("ref_high"), f"observations[{i}].ref_high", errors),
            abnormal_flag=flag,
        )
        if obs.value_num is None and not obs.value_text:
            errors.append(f"observations[{i}] 需要 value_num 或 value_text 至少其一")
            continue
        observations.append(obs)

    if doc_type == "lab_report" and not observations:
        errors.append("lab_report 至少应抽取到 1 条 observations")
    if doc_type == "ultrasound_report" and not (findings or data.get("impressions")):
        errors.append("ultrasound_report 应抽取 findings 或 impressions")

    if errors:
        raise ValueError("；".join(errors))

    return ExtractionResult(
        document_type=doc_type,
        exam_date=exam_date,
        patient=patient,
        exam_info=exam_info,
        findings=findings,
        impressions=_str_list(data.get("impressions"), "impressions", errors),
        observations=observations,
        notes=(str(data["notes"]).strip() if data.get("notes") else None),
        deidentified=bool(data.get("deidentified", False)),
        engine=(str(data["engine"]).strip() if data.get("engine") else None),
    )
