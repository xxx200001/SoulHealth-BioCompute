"""健康问答：基于患者档案上下文，真实调用 Claude 回答问题。

原则：
- 无 ANTHROPIC_API_KEY 或处于显式 MOCK 时，直接抛 QAUnavailable 说明原因，
  绝不返回预制的假回答；
- 发送给模型的上下文只含化名与结构化健康数据（真实姓名仅存本地库，不出境）；
- 回答落地前过 compliance.lint 合规校验；命中违禁表述则带着违规反馈重试一次，
  仍不合规就如实报错，不放行。
"""
from __future__ import annotations

import json
from typing import List

from .. import config
from ..archive import repository as repo
from ..reportgen import compliance

MAX_TOKENS = 1200

SYSTEM_PROMPT = """你是 SOULHEALTH 健康科研平台的健康档案问答助手。你基于用户提供的【健康档案】回答健康管理问题。

必须遵守：
1. 你不是医生，只做健康科普与生活方式建议；不下诊断、不开处方、不解读为确诊结论。
2. 档案中的"历次分析记录"与"指标历史趋势"按时间提供了纵向数据——如果用户问"是否好转""和上次比怎么样"
   之类需要对比的问题，请基于这些历史数据具体作答（引用日期与数值），而不是只看最新一次；
   若历史记录不足以支撑判断（如只有一次记录），请如实说明数据不足，不要臆测趋势。
3. 涉及疾病判断、用药、剂量调整的问题，明确建议咨询医生；指标显著异常时提醒尽早就诊。
4. 禁止使用绝对化疗效表述：不得出现"速效/根治/治愈/包好/无任何副作用/彻底解决/百分之百/保证/几天见效"等承诺性词语。
5. 回答要引用档案中的具体数据（指标名/数值/参考范围/日期），说清"依据是什么"；档案中没有的信息不要编造，直接说明档案中未见。
6. 用简体中文，语气专业、克制、友善；控制在 400 字以内；适当分点但不过度罗列。
7. 结尾无需重复免责声明（系统会统一附加）。"""


class QAUnavailable(RuntimeError):
    """问答不可用（未配置密钥 / 显式 MOCK 模式）。"""


MAX_HISTORY_ANALYSES = 5  # 问答上下文中携带的历史分析条数上限，避免上下文过长


def _context(pid: str) -> dict:
    """构造脱敏档案上下文：化名 + 结构化数据 + **历次分析趋势**，绝不包含真实姓名。

    历史分析不是只取"最近一次"，而是取最近若干次的时间序列，让模型能回答
    "我的肝酶是不是在好转""上次和这次相比怎么样"这类需要纵向对比的问题。
    """
    snap = repo.snapshot(pid)
    p = snap["patient"]
    latest = [
        {"code": o["code"], "display": o.get("display"), "value": o.get("value_num"),
         "unit": o.get("unit"), "ref": [o.get("ref_low"), o.get("ref_high")],
         "flag": o.get("abnormal_flag"), "date": o.get("observed_at")}
        for o in snap["observations_latest"].values()
    ]

    analyses = repo.list_analyses(pid)  # 按时间倒序
    history: List[dict] = []
    for row in analyses[:MAX_HISTORY_ANALYSES]:
        detail = repo.get_analysis(row["id"]) or {}
        history.append({
            "分析时间": row["created_at"],
            "风险标签": [t["label"] for t in (detail.get("risk_tags") or [])],
            "自述证型参考": [s["label"] for s in (detail.get("syndrome_tags") or [])],
            "本次组方原料": [i["display"] for i in
                          ((detail.get("formula") or {}).get("ingredients") or [])],
        })

    # 同一指标跨次数值变化（便于回答"是否好转"类问题），仅取有 ≥2 次记录的指标
    trend: dict = {}
    for code, series in _group_timeline_by_code(snap["observations_timeline"]).items():
        if len(series) >= 2:
            trend[code] = [{"date": s["observed_at"], "value": s["value_num"],
                           "flag": s["abnormal_flag"]} for s in series[-6:]]

    return {
        "患者": {"化名": p["pseudonym"], "性别": p.get("sex"),
                 "年龄": p.get("age_years"), "身高cm": p.get("height_cm"),
                 "体重kg": p.get("weight_kg")},
        "最新指标": latest,
        "指标历史趋势（同一指标跨多次记录的数值变化）": trend,
        "影像提示": [i["text"] for i in snap["impressions"]],
        "影像所见异常": [f"{f['organ']}：{'、'.join(f['flags'])}"
                        for f in snap["findings"] if f.get("flags")],
        "患者自述": [n["text"] for n in snap["notes"]],
        f"历次分析记录（最近 {len(history)} 次，按时间倒序）": history,
    }


def _group_timeline_by_code(timeline: List[dict]) -> dict:
    grouped: dict = {}
    for obs in timeline:
        grouped.setdefault(obs["code"], []).append(obs)
    return grouped


def ask(pid: str, question: str) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    if config.MOCK_MODE:
        raise QAUnavailable(
            "当前为显式 MOCK 演示模式：健康问答需要真实模型，不提供预制假回答。"
            "请配置 ANTHROPIC_API_KEY 并去掉 SOULHEALTH_MOCK=1 后使用。")
    if not config.ANTHROPIC_API_KEY:
        raise QAUnavailable(
            "健康问答不可用：未配置 ANTHROPIC_API_KEY。请在 .env 填入密钥后重试。")

    import anthropic  # 惰性导入

    ctx = json.dumps(_context(pid), ensure_ascii=False)
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def _call(extra: str = "") -> str:
        resp = client.messages.create(
            model=config.LLM_MODEL, max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       f"【健康档案（已脱敏）】\n{ctx}\n\n【用户问题】\n{question}{extra}"}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    answer = _call()
    hits = compliance.lint(answer)
    if hits:
        words = "；".join(hits[:3])
        answer = _call(f"\n\n【系统合规反馈】你上一版回答命中了违禁表述（{words}），"
                       "请重写回答，严格避免任何绝对化疗效承诺。")
        hits = compliance.lint(answer)
        if hits:
            raise RuntimeError("回答未通过合规校验，已拦截。请换一种问法重试。")

    return {
        "answer": answer,
        "disclaimer": "以上内容为健康科普与生活方式建议，不替代医疗诊断与处方；"
                      "如有不适或指标异常，请及时就医并遵医嘱随访。",
        "model": config.LLM_MODEL,
    }
