"""文档写入器：统一"块模型" → .docx（纯标准库直写 OOXML）与 .md。

块模型（blocks 为列表，每项为 tuple）：
  ("title", 文本)                        文档大标题（居中）
  ("h1"|"h2"|"h3", 文本)                 各级标题（带大纲级别，Word 导航可用）
  ("p", 文本 或 [(文本, 加粗bool), ...])   段落 / 富文本段落
  ("bullet", 文本)                       单条要点（• 前缀 + 悬挂缩进）
  ("table", {"header": [...], "rows": [[...], ...]})
  ("note", 文本)                         提示框（浅底色单元格）

.docx 本质是 zip + XML；此写入器零三方依赖，服务器与沙箱皆可直接出
Word 文件。已通过 LibreOffice 渲染与 OOXML 校验（见阶段三测试）。
"""
from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Tuple, Union

Runs = Union[str, List[Tuple[str, bool]]]

# ---------------------------------------------------------------- XML 工具

def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _runs(content: Runs, base_rpr: str = "") -> str:
    pairs = [(content, False)] if isinstance(content, str) else content
    out = []
    for text, bold in pairs:
        rpr = base_rpr + ("<w:b/>" if bold else "")
        rpr_xml = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        out.append(f'<w:r>{rpr_xml}<w:t xml:space="preserve">{_esc(text)}</w:t></w:r>')
    return "".join(out)


def _p(content: Runs, style: str = "", ppr_extra: str = "") -> str:
    ppr = ""
    if style or ppr_extra:
        style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
        ppr = f"<w:pPr>{style_xml}{ppr_extra}</w:pPr>"
    return f"<w:p>{ppr}{_runs(content)}</w:p>"


def _bullet(text: str) -> str:
    ppr = ('<w:pPr><w:spacing w:after="60"/>'
           '<w:ind w:left="480" w:hanging="240"/></w:pPr>')
    return f"<w:p>{ppr}{_runs('• ' + text)}</w:p>"


def _note(text: str) -> str:
    """提示框：单格表格 + 浅色底纹。"""
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
        '<w:tblBorders>' + "".join(
            f'<w:{s} w:val="single" w:sz="4" w:color="BFD3E6"/>'
            for s in ("top", "left", "bottom", "right")) +
        '</w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="9000"/></w:tblGrid>'
        '<w:tr><w:tc><w:tcPr>'
        '<w:shd w:val="clear" w:color="auto" w:fill="EEF4FB"/>'
        '<w:tcMar><w:top w:w="80" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>'
        f'</w:tcPr>{_p([("提示：", True), (text, False)])}</w:tc></w:tr></w:tbl>'
        '<w:p/>'
    )


def _table(spec: dict) -> str:
    header: List[str] = spec.get("header", [])
    rows: List[List[str]] = spec.get("rows", [])
    ncols = max(len(header), max((len(r) for r in rows), default=1), 1)
    width = 9000 // ncols
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(ncols))
    borders = "".join(
        f'<w:{s} w:val="single" w:sz="4" w:color="999999"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV"))

    def cell(text: str, bold: bool, fill: str = "") -> str:
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>' if fill else ""
        tcpr = (f'<w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shd}</w:tcPr>')
        content: Runs = [(str(text), bold)]
        return f"<w:tc>{tcpr}{_p(content)}</w:tc>"

    xml = [f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
           f'<w:tblBorders>{borders}</w:tblBorders></w:tblPr>'
           f'<w:tblGrid>{grid}</w:tblGrid>']
    if header:
        xml.append("<w:tr>" + "".join(
            cell(h, True, "E7EEF7") for h in (header + [""] * (ncols - len(header)))
        ) + "</w:tr>")
    for row in rows:
        xml.append("<w:tr>" + "".join(
            cell(c, False) for c in (list(row) + [""] * (ncols - len(row)))
        ) + "</w:tr>")
    xml.append("</w:tbl><w:p/>")
    return "".join(xml)


# ---------------------------------------------------------------- 静态部件

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:docDefaults><w:rPrDefault><w:rPr>
  <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/>
  <w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault>
  <w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/>
  </w:pPr></w:pPrDefault></w:docDefaults>
 <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
 <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="120" w:after="240"/><w:jc w:val="center"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="34"/><w:rFonts w:eastAsia="微软雅黑"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="28"/><w:rFonts w:eastAsia="微软雅黑"/><w:color w:val="1F4E79"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="180" w:after="90"/><w:outlineLvl w:val="1"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="24"/><w:rFonts w:eastAsia="微软雅黑"/><w:color w:val="2E5F8F"/></w:rPr></w:style>
 <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>
  <w:basedOn w:val="Normal"/>
  <w:pPr><w:spacing w:before="120" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr>
  <w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
</w:styles>"""

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
 <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
 <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
 <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _core_xml(title: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{_esc(title)}</dc:title>'
        '<dc:creator>SOULHEALTH Demo</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        '</cp:coreProperties>'
    )


_SECT = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
         '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"'
         ' w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')


# ---------------------------------------------------------------- 对外接口

def blocks_to_docx(blocks: Iterable[tuple], path, title: str = "") -> str:
    body: List[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "title":
            body.append(_p(block[1], style="Title"))
        elif kind in ("h1", "h2", "h3"):
            body.append(_p(block[1], style=f"Heading{kind[1]}"))
        elif kind == "p":
            body.append(_p(block[1]))
        elif kind == "bullet":
            body.append(_bullet(block[1]))
        elif kind == "table":
            body.append(_table(block[1]))
        elif kind == "note":
            body.append(_note(block[1]))
        else:
            raise ValueError(f"未知块类型: {kind}")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}{_SECT}</w:body></w:document>"
    )
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
        zf.writestr("docProps/core.xml", _core_xml(title))
    return str(path)


def blocks_to_markdown(blocks: Iterable[tuple]) -> str:
    lines: List[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "title":
            lines += [f"# {block[1]}", ""]
        elif kind in ("h1", "h2", "h3"):
            lines += [f"{'#' * (int(kind[1]) + 1)} {block[1]}", ""]
        elif kind == "p":
            content = block[1]
            if isinstance(content, str):
                lines += [content, ""]
            else:
                lines += ["".join(f"**{t}**" if b else t for t, b in content), ""]
        elif kind == "bullet":
            lines.append(f"- {block[1]}")
        elif kind == "note":
            lines += [f"> 提示：{block[1]}", ""]
        elif kind == "table":
            spec = block[1]
            header = spec.get("header", [])
            rows = spec.get("rows", [])
            if header:
                lines.append("| " + " | ".join(map(str, header)) + " |")
                lines.append("|" + "---|" * len(header))
            for row in rows:
                lines.append("| " + " | ".join(map(str, row)) + " |")
            lines.append("")
    return "\n".join(lines).strip() + "\n"
