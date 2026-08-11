"""合规守卫：报告生成的最后一道闸。

FORBIDDEN_PATTERNS 覆盖《广告法》《食品安全法》语境下食品/药食同源产品
不得使用的绝对化疗效表述；REQUIRED_SNIPPETS 为每份对外报告必须包含的
要素（不替代诊断声明、就医随访提示）。generate 前 assert_clean()，
违规直接抛错阻断输出 —— 测试用例同样引用本清单做红线回归。
"""
from __future__ import annotations

import re
from typing import List

FORBIDDEN_PATTERNS = [
    r"速效", r"根治", r"治愈", r"彻底逆转", r"彻底杜绝", r"彻底消除",
    r"无任何副作用", r"零副作用", r"无副作用", r"无毒副作用",
    r"保证.{0,6}(降|瘦|恢复|正常)", r"百分之百", r"100%\s*(有效|见效)",
    r"永不反弹", r"包治", r"替代(药物|治疗)", r"无需(就医|吃药|服药)",
    r"停药",
    r"\d+\s*天.{0,8}(下降|降低|减重|消失|恢复正常)",   # 数字化时间承诺
    r"(ALT|GGT|转氨酶).{0,10}下降\s*\d+\s*%",
]

REQUIRED_SNIPPETS = ["不替代", "随访"]

_COMPILED = [re.compile(p) for p in FORBIDDEN_PATTERNS]


def lint(text: str) -> List[str]:
    """返回命中的违规片段列表（含少量上下文），空列表 = 通过。"""
    hits: List[str] = []
    for pat in _COMPILED:
        for m in pat.finditer(text or ""):
            start = max(0, m.start() - 6)
            hits.append(f"{pat.pattern} → …{text[start:m.end() + 6]}…")
    return hits


def missing_required(text: str) -> List[str]:
    return [s for s in REQUIRED_SNIPPETS if s not in (text or "")]


def find_violations(text: str) -> list:
    """返回文本中命中的违禁话术列表（空列表=合规）。供 AI 生成内容的前置校验。"""
    hits = []
    for pat in _COMPILED:
        for m in pat.finditer(text or ""):
            if m.group(0) not in hits:
                hits.append(m.group(0))
    return hits


def assert_clean(text: str, doc_name: str = "") -> None:
    hits = lint(text)
    if hits:
        raise ValueError(f"合规校验未通过（{doc_name}）：" + "；".join(hits[:5]))
    miss = missing_required(text)
    if miss:
        raise ValueError(f"合规校验未通过（{doc_name}）：缺少必备要素 {miss}")
