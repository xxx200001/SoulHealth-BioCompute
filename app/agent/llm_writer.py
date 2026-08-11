"""LLM 文字润色钩子（可选增强，模板文案为主、LLM 为辅）。

设计原则：报告内容由结构化数据 + 模板确定性生成（保证合规、可复现），
LLM 仅用于润色个别叙述段落。MOCK 模式（默认）直接透传模板文案。
润色输出会再过一遍 compliance.lint，任何违规词直接回退原文——
即使模型"发挥"，也出不了合规红线。
"""
from __future__ import annotations

from .. import config

_SYSTEM = (
    "你是医疗健康文案润色助手。仅对给定段落做语言润色：更通顺、更易读。"
    "硬性红线：不得新增任何疗效承诺、时间承诺、数字承诺；不得出现"
    "速效/根治/治愈/彻底/无任何副作用/保证 等绝对化表述；"
    "不得删除任何就医、随访、禁忌相关内容。只输出润色后的文本。"
)


def polish(text: str) -> str:
    if config.MOCK_MODE or not text.strip():
        return text
    try:
        import anthropic
        from ..reportgen import compliance

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.LLM_MODEL, max_tokens=1200, system=_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        out = "".join(b.text for b in resp.content
                      if getattr(b, "type", "") == "text").strip()
        if not out or compliance.lint(out):
            return text  # 润色结果违规或为空 → 回退模板原文
        return out
    except Exception:
        return text  # 任何异常都不阻断报告生成
