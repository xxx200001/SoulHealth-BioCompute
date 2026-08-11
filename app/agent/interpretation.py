"""AI 综合解读：由真实大模型通读结构化分析结果后生成的叙述性解读。

定位与真实性边界（与 qa.py 同一原则）：
- 规则引擎产出的风险识别/机制链/组方是结构化事实层；本模块补的是
  "AI 读懂了整份档案之后的综合叙述"——各异常之间的关联（如尿酸+肌酐+
  贫血的肾脏轴）、轻重缓急排序、生活方式落点与就医指引；
- 仅在配置 ANTHROPIC_API_KEY 且非 MOCK 模式时真实调用模型生成；
  MOCK / 无密钥时返回 available=False 与如实的原因说明，
  **绝不返回预制的假解读冒充 AI 生成**；
- 生成内容经 compliance 合规闸校验（禁绝对化疗效表述），违规即丢弃并如实报错；
- 解读随分析入库（可回放），并写入《个性化健康分析报告》独立章节，
  章节内明确标注"由大模型生成，供参考，不构成诊疗意见"。
"""
from __future__ import annotations

from typing import List, Optional

from .. import config
from ..reportgen import compliance

MAX_TOKENS = 900

_SYSTEM = """你是一名严谨的健康管理师，基于给定的结构化分析结果撰写一段综合解读。要求：
1. 通读全部数据后给出"整体图景"：把各项异常之间的内在关联讲清楚（例如尿酸升高、肌酐升高与血红蛋白偏低可能共同指向肾脏这条轴），而不是逐条复述指标；
2. 给出轻重缓急：哪一项最需要优先就医处理、哪些属于随访观察，理由是什么；
3. 结合已识别的证型与代茶饮方，用一两句话说明食养方向为何这样选（但明确食养仅为辅助）；
4. 落到可执行的生活方式建议（3 条以内，具体到行为）与就医指引（去哪个科、带什么资料、多久复查）；
5. 引用具体数值与参考范围，说清依据；数据中没有的信息不得编造；
6. 禁止下诊断、禁止开处方、禁止绝对化疗效表述（根治/治愈/保证/特效等）；
7. 简体中文，语气专业克制，450 字以内，分段书写，不用列表符号。"""


class InterpretUnavailable(RuntimeError):
    """综合解读不可用（未配置密钥 / 显式 MOCK 模式）。"""


def _context(snapshot: dict, risk_tags: List[dict], chain: dict,
             formula: dict, syndrome_tags: List[dict]) -> str:
    p = snapshot["patient"]
    import json
    ctx = {
        "患者": {"化名": p["pseudonym"], "性别": p.get("sex"),
                 "年龄": p.get("age_years"), "BMI相关": {
                     "身高cm": p.get("height_cm"), "体重kg": p.get("weight_kg")}},
        "最新指标": [
            {"code": o["code"], "名称": o.get("display"), "值": o.get("value_num")
             if o.get("value_num") is not None else o.get("value_text"),
             "单位": o.get("unit"), "参考": [o.get("ref_low"), o.get("ref_high")],
             "异常": o.get("abnormal_flag")}
            for o in snapshot["observations_latest"].values()],
        "影像提示": [i["text"] for i in snapshot.get("impressions", [])],
        "自述": [n["text"] for n in snapshot.get("notes", [])],
        "系统识别的风险": [
            {"标签": t["label"], "级别": t["severity"], "依据": t["evidence"]}
            for t in risk_tags],
        "自述证型参考": [s["label"] for s in syndrome_tags],
        "机制链要点": {
            "生物机制": next((l["items"] for l in chain.get("levels", [])
                             if l["level"] == "生物机制"), []),
            "风险方向": next((l["items"] for l in chain.get("levels", [])
                             if l["level"] == "风险方向"), [])},
        "代茶饮方": {
            "方名": formula.get("formula_name"),
            "治则": formula.get("treatment_principle"),
            "组成": [f"{i['display']}{i['grams']}g"
                     for i in formula.get("ingredients", [])]}
        if formula.get("ingredients") else "本次未组方",
    }
    return json.dumps(ctx, ensure_ascii=False, indent=1)


def generate(snapshot: dict, risk_tags: List[dict], chain: dict,
             formula: dict, syndrome_tags: Optional[List[dict]] = None) -> dict:
    """返回 {"available": bool, "text"|"reason": str, "model": str|None}。
    不可用时不抛异常（分析主流程不因解读缺席而失败），由调用方如实展示原因。"""
    syndrome_tags = syndrome_tags or []
    if config.MOCK_MODE:
        return {"available": False, "model": None,
                "reason": "当前为显式 MOCK 演示模式：AI 综合解读需要真实大模型通读"
                          "档案后生成，不提供预制假解读。配置 ANTHROPIC_API_KEY 并"
                          "去掉 SOULHEALTH_MOCK=1 后，此处将展示真实生成的综合解读。"}
    if not config.ANTHROPIC_API_KEY:
        return {"available": False, "model": None,
                "reason": "AI 综合解读未启用：未配置 ANTHROPIC_API_KEY。"
                          "该环节由真实大模型通读本次全部结构化结果后撰写"
                          "（异常关联、轻重缓急、就医指引），不使用模板文案。"}
    try:
        import anthropic  # 惰性导入
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=config.LLM_MODEL, max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content":
                       "以下是本次分析的全部结构化结果，请撰写综合解读：\n"
                       + _context(snapshot, risk_tags, chain, formula,
                                  syndrome_tags)}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        if not text:
            return {"available": False, "model": config.LLM_MODEL,
                    "reason": "模型返回为空，本次未生成综合解读（不以模板顶替）。"}
        # 合规闸：违规即丢弃，不让绝对化表述流入报告
        bad = compliance.find_violations(text)
        if bad:
            return {"available": False, "model": config.LLM_MODEL,
                    "reason": f"模型生成内容未通过合规校验（{('、'.join(bad))[:60]}），"
                              f"已丢弃本次解读；可重新运行分析重试。"}
        return {"available": True, "model": config.LLM_MODEL, "text": text}
    except Exception as exc:  # 网络/配额等：如实报错，不降级为假内容
        return {"available": False, "model": config.LLM_MODEL,
                "reason": f"AI 综合解读调用失败：{exc}（不以模板顶替，可重试）"}
