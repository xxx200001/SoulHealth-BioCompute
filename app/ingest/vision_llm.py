"""视觉 LLM 抽取引擎：图片/PDF → 结构化 JSON（默认 Claude，可换任何视觉模型）。

要点：
- MOCK 模式（显式 SOULHEALTH_MOCK=1）：按文件名路由本地样例，离线可完整演示。
- 真实模式：前置校验（非空 / 魔数嗅探真实格式 / 体积上限）→ base64 发送 →
  严格 JSON 输出；schema 校验失败时把错误回喂模型自修正一次（共 2 次尝试）。
- **"模型没收到图片"是一类独立故障**：当模型答复表明它未看到任何图像时，
  立刻中止并抛出可执行的诊断信息（而不是含糊的"未通过校验"），并提示用
  /api/selftest/vision 自检。常见成因：所配模型不支持视觉、网关/代理剥离了
  非文本块、密钥指向纯文本端点。
- anthropic 为惰性导入：不装该包也能跑 MOCK 全流程。
"""
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Optional, Tuple

from .. import config
from ..schemas import ExtractionResult, from_dict
from .prompts import EXTRACTION_SYSTEM, extraction_user_prompt, repair_prompt

_LAB_NAME_HINTS = ("lab", "blood", "化验", "肝功", "血", "生化", "检验")
_METABOLIC_HINTS = ("糖", "血脂", "代谢", "尿酸", "metab", "glu")

# Anthropic 单图上限约 5MB（base64 后），留出 33% 膨胀余量
MAX_IMAGE_BYTES = 3_600_000
MAX_PDF_BYTES = 30_000_000

# 魔数 → media_type（不信任扩展名：手机改名、截图另存都可能对不上）
_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF", "application/pdf"),
)

# 模型表示"我没看到图片"的典型说法（仅在 JSON 解析失败后才做此判断）
_NO_IMAGE_PATTERNS = [
    r"没有(任何)?图片", r"未(收到|提供|附上|看到)(任何)?图片", r"看不到(任何)?图片",
    r"没有图像", r"未见图片", r"只有文字", r"没有可抽取的内容", r"输入缺失",
    r"图片(未|没有)(上传|附加)", r"no image", r"没有单据", r"未附(带)?图",
    r"(cannot|can't|couldn't|unable to)\s+(see|view|access|find)[^.]{0,24}image",
]
_NO_IMAGE_RE = [re.compile(p, re.I) for p in _NO_IMAGE_PATTERNS]


class ExtractionError(RuntimeError):
    pass


class VisionInputError(ExtractionError):
    """文件本身不可用（空文件 / 格式不支持 / 超出体积上限）。"""


class VisionNotSeeingImageError(ExtractionError):
    """请求已送达且模型有回复，但模型表示未收到图像——通常是模型或网关问题。"""


# ---------------------------------------------------------------- MOCK

def _load_mock(file_path: Path) -> ExtractionResult:
    name = file_path.name.lower()
    if any(h in name for h in _METABOLIC_HINTS):
        sample = "sample_metabolic_extraction.json"   # 第二病种：糖脂/尿酸
    elif any(h in name for h in _LAB_NAME_HINTS):
        sample = "sample_lab_extraction.json"
    else:
        sample = "sample_ultrasound_extraction.json"
    data = json.loads((config.SAMPLE_DIR / sample).read_text(encoding="utf-8"))
    data["engine"] = "mock"
    return from_dict(data)


# ---------------------------------------------------------------- 工具

def _strip_to_json(text: str) -> str:
    """剥掉可能出现的 ```json 围栏，截取首个 { 到最后一个 }。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"输出中未找到 JSON 对象：{(text or '')[:120]!r}...")
    return t[start:end + 1]


def _looks_like_no_image(text: str) -> bool:
    return any(p.search(text or "") for p in _NO_IMAGE_RE)


def sniff_media_type(data: bytes, file_path: Path) -> Optional[str]:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # 魔数不识别时退回扩展名（少数被处理过的图片会丢头部特征）
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif",
            ".pdf": "application/pdf"}.get(file_path.suffix.lower())


def _build_source_block(file_path: Path) -> Tuple[dict, dict]:
    """前置校验并构造内容块。返回 (block, diagnostics)。"""
    data = file_path.read_bytes()
    if not data:
        raise VisionInputError(
            f"{file_path.name} 是空文件（0 字节），无法抽取。请重新上传原始图片。")

    media_type = sniff_media_type(data, file_path)
    if media_type is None:
        raise VisionInputError(
            f"{file_path.name} 的实际格式无法识别（文件头 {data[:8]!r}）。"
            "支持 jpg / png / webp / gif / pdf；若是 HEIC 等手机原生格式，"
            "请先转存为 JPG 或 PNG 再上传。")

    is_pdf = media_type == "application/pdf"
    limit = MAX_PDF_BYTES if is_pdf else MAX_IMAGE_BYTES
    if len(data) > limit:
        raise VisionInputError(
            f"{file_path.name} 体积 {len(data) / 1e6:.1f}MB 超出上限 "
            f"{limit / 1e6:.1f}MB。请压缩后重试（手机拍照建议导出为"
            "「中等质量」JPG，一般 1MB 以内即可清晰识别）。")

    b64 = base64.b64encode(data).decode("ascii")
    diag = {"filename": file_path.name, "bytes": len(data),
            "media_type": media_type, "b64_len": len(b64),
            "model": config.LLM_MODEL}
    if is_pdf:
        return ({"type": "document",
                 "source": {"type": "base64", "media_type": media_type, "data": b64}},
                diag)
    return ({"type": "image",
             "source": {"type": "base64", "media_type": media_type, "data": b64}},
            diag)


def _diag_text(diag: dict) -> str:
    return (f"（诊断：文件 {diag['filename']}，{diag['bytes']} 字节，"
            f"media_type={diag['media_type']}，base64 {diag['b64_len']} 字符，"
            f"模型 {diag['model']}）")


# ---------------------------------------------------------------- 主流程

def extract_from_file(file_path, doc_type_hint: Optional[str] = None) -> ExtractionResult:
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))

    if config.MOCK_MODE:
        return _load_mock(file_path)

    if not config.ANTHROPIC_API_KEY:
        raise ExtractionError(
            "真实抽取不可用：未配置 ANTHROPIC_API_KEY。"
            "请在 .env 填入密钥启用 Claude 视觉抽取；"
            "如需离线演示请显式设置 SOULHEALTH_MOCK=1（演示样例会明确标注）。"
        )

    import anthropic  # 惰性导入：MOCK 模式无需安装

    source_block, diag = _build_source_block(file_path)
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    messages = [{
        "role": "user",
        "content": [source_block,
                    {"type": "text", "text": extraction_user_prompt(doc_type_hint)}],
    }]

    last_error: Optional[Exception] = None
    for _attempt in range(2):  # 首次 + 一次自修正
        resp = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=3000,
            system=EXTRACTION_SYSTEM,
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(_strip_to_json(text))
            data["engine"] = "vision_llm"
            return from_dict(data)
        except (ValueError, json.JSONDecodeError) as exc:
            # 关键分支：模型明确表示"没看到图片" → 不是格式问题，重试也无用
            if _looks_like_no_image(text):
                raise VisionNotSeeingImageError(
                    f"请求已送达模型，但模型答复它没有收到图像，因此本次抽取中止"
                    f"（未编造任何数据）。{_diag_text(diag)} "
                    f"常见原因：① 所配模型 {config.LLM_MODEL} 不支持图像输入，"
                    f"请改用视觉模型（如 claude-sonnet-4-6）；"
                    f"② 中间网关/代理剥离了图像块；③ 密钥指向纯文本端点。"
                    f"请调用 GET /api/selftest/vision 一键自检确认。"
                    f"模型原话：{(text or '').strip()[:160]}"
                ) from exc
            last_error = exc
            messages.append({"role": "assistant", "content": text or "(空)"})
            messages.append({"role": "user", "content": repair_prompt(str(exc))})

    raise ExtractionError(
        f"视觉抽取两次尝试均未通过 schema 校验：{last_error} {_diag_text(diag)}")


# ---------------------------------------------------------------- 视觉自检

def _probe_png(color: Tuple[int, int, int] = (220, 30, 30), size: int = 48) -> bytes:
    """纯标准库生成一张纯色 PNG，用于探测链路是否真的支持图像输入。"""
    raw = b"".join(b"\x00" + bytes(color) * size for _ in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def vision_selftest() -> dict:
    """发一张已知颜色的纯色图问模型看到什么颜色，判定链路是否支持视觉。

    返回 {ok, mode, model, reply, reason}；不抛异常，供接口与命令行直接展示。
    """
    base = {"model": config.LLM_MODEL, "mode": config.LLM_MODE}
    if config.MOCK_MODE:
        return {**base, "ok": False,
                "reason": "当前为显式 MOCK 模式，未连接真实模型，无需也无法自检视觉。"}
    if not config.ANTHROPIC_API_KEY:
        return {**base, "ok": False,
                "reason": "未配置 ANTHROPIC_API_KEY，无法自检。"}
    try:
        import anthropic
    except ImportError:
        return {**base, "ok": False,
                "reason": "未安装 anthropic 包：pip install -r requirements.txt"}

    png = _probe_png()
    b64 = base64.b64encode(png).decode("ascii")
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.LLM_MODEL, max_tokens=64,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png", "data": b64}},
                {"type": "text", "text": "这张图是什么颜色？只回答颜色名，两个字以内。"
                                         "如果你没有收到图片，请直接回答「无图」。"}]}],
        )
        reply = "".join(b.text for b in resp.content
                        if getattr(b, "type", "") == "text").strip()
    except Exception as exc:
        return {**base, "ok": False, "reply": None,
                "reason": f"调用失败：{exc}。请检查密钥、模型名与出网连通性。"}

    if _looks_like_no_image(reply) or "无图" in reply:
        return {**base, "ok": False, "reply": reply,
                "reason": f"模型收不到图像：{config.LLM_MODEL} 可能不支持视觉输入，"
                          "或中间网关剥离了图像块。请改用视觉模型"
                          "（SOULHEALTH_LLM_MODEL=claude-sonnet-4-6）或检查代理配置。"}
    if any(k in reply for k in ("红", "赤", "red", "Red")):
        return {**base, "ok": True, "reply": reply,
                "reason": "视觉链路正常：模型正确识别了探测图颜色，可正常上传报告图片。"}
    return {**base, "ok": False, "reply": reply,
            "reason": "模型有回复但未能正确识别探测图颜色（预期为红色），"
                      "视觉链路可疑，请结合上面的原始回复排查。"}
