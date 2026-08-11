"""报告生成入口：由分析上下文产出两份文档 × 两种格式（docx + md），
经合规闸校验后写盘并登记 reports 表。
"""
from __future__ import annotations

from .. import config
from ..archive import repository as repo
from . import compliance, docx_writer, health_report, tea_plan

_BUILDERS = {
    "health_analysis": ("个性化健康分析报告", health_report.build_blocks),
    "tea_plan": ("药食同源代茶饮建议", tea_plan.build_blocks),
}


def generate_all(ctx: dict) -> list:
    """ctx 需包含 analysis_id / snapshot / risk_tags / mechanism_chain /
    biocompute_plan / formula。返回 reports 行列表（含 report_id 与路径）。"""
    out = []
    aid8 = ctx["analysis_id"][:8]
    for rtype, (title, builder) in _BUILDERS.items():
        if rtype == "tea_plan" and not (ctx.get("formula") or {}).get("ingredients"):
            continue  # 无风险标签 → 不硬凑配方，也不出茶饮报告
        blocks = builder(ctx)
        md_text = docx_writer.blocks_to_markdown(blocks)
        compliance.assert_clean(md_text, doc_name=title)  # 合规闸：违规即阻断

        md_path = config.REPORT_DIR / f"{rtype}_{aid8}.md"
        md_path.write_text(md_text, encoding="utf-8")
        docx_path = config.REPORT_DIR / f"{rtype}_{aid8}.docx"
        docx_writer.blocks_to_docx(blocks, docx_path, title=title)

        for fmt, path in (("md", md_path), ("docx", docx_path)):
            rid = repo.save_report(ctx["analysis_id"], ctx["patient_id"],
                                   rtype, fmt, str(path))
            out.append({"report_id": rid, "report_type": rtype, "title": title,
                        "format": fmt, "path": str(path),
                        "download_url": f"/api/reports/{rid}/download"})
    return out
