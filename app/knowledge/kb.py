"""知识库加载与《药食同源目录》校验。

catalog_check() 是需求文档"医学知识匹配"的落地之一：
配方中的每味原料都会核对目录状态，目录外原料自动标出并给出替代建议。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_KB_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def catalog() -> Dict[str, dict]:
    data = json.loads((_KB_DIR / "yshy_catalog.json").read_text(encoding="utf-8"))
    return {item["name"]: item for item in data["ingredients"]}


@lru_cache(maxsize=1)
def medical() -> dict:
    return json.loads((_KB_DIR / "medical_knowledge.json").read_text(encoding="utf-8"))


def get_ingredient(name: str) -> Optional[dict]:
    return catalog().get(name)


def in_catalog(name: str) -> bool:
    """目录门禁判定：status 以「药食同源目录」开头即视为目录内（含 2002 名单、
    历年试点与增补批次的后缀标注）。玉米须（保健食品原料目录）、茯神（目录收录名
    为茯苓）、其余任何非该前缀的状态均判为目录外，触发组方自动替换。"""
    item = get_ingredient(name)
    return bool(item and str(item.get("status", "")).startswith("药食同源目录"))


def catalog_check(names: List[str]) -> List[dict]:
    """逐味核对目录状态。返回 [{name, status, ok, note}]。"""
    results = []
    for name in names:
        item = get_ingredient(name)
        if item is None:
            results.append({"name": name, "status": "未收录于本地知识库",
                            "ok": False, "note": "请人工核对卫健委最新公告"})
            continue
        ok = str(item.get("status", "")).startswith("药食同源目录")
        note = "" if ok else item.get("modern", "")
        results.append({"name": name, "status": item["status"], "ok": ok, "note": note})
    return results


def condition_knowledge(tag_id: str) -> Optional[dict]:
    return medical()["conditions"].get(tag_id)
