"""摄取管线编排：文件 → 抽取引擎 → 脱敏 → 校验 → 落库。

对外只暴露 ingest_document()；上层（API/前端/测试）无需关心引擎细节。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import config
from ..archive import repository as repo
from . import deid
from .vision_llm import extract_from_file as _vision_extract


def ingest_document(patient_id: str, file_path, doc_type_hint: Optional[str] = None,
                    engine: Optional[str] = None,
                    source_filename: Optional[str] = None) -> dict:
    """摄取一份单据并归档。

    参数
    ----
    patient_id     目标患者
    file_path      本地文件路径（jpg/png/webp/pdf）
    doc_type_hint  可选类型提示：ultrasound_report / lab_report / ...
    engine         可选覆盖引擎：vision_llm / paddleocr（默认取配置）

    返回 {document_id, engine, mock_mode, extraction}
    """
    file_path = Path(file_path)
    engine = (engine or config.OCR_ENGINE).strip()

    if engine == "paddleocr":
        from .ocr_fallback import extract_from_image  # 惰性：未装 paddle 不影响默认路径
        extraction = extract_from_image(str(file_path), doc_type_hint)
    else:
        extraction = _vision_extract(file_path, doc_type_hint)

    extraction = deid.scrub_extraction(extraction)

    doc_id = repo.save_document(
        pid=patient_id,
        source_filename=source_filename or file_path.name,
        stored_path=str(file_path),
        extraction=extraction,
    )
    return {
        "document_id": doc_id,
        "engine": extraction.engine,
        "llm_mode": config.LLM_MODE,
        "mock_mode": config.MOCK_MODE,
        "extraction": extraction.to_dict(),
    }
