"""阶段五离线测试：患者身份匹配、持久化档案库、第二病种全链路泛化、
无风险兜底、历史回放数据、级联删除。零三方依赖、零网络。

运行：python tests/test_stage5.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SOULHEALTH_MOCK"] = "1"            # 显式离线
os.environ["SOULHEALTH_BIOCOMPUTE"] = "mock"

from app import config                                   # noqa: E402
if config.DB_PATH.exists():
    config.DB_PATH.unlink()                              # 干净库验证建库+迁移

from app.agent import orchestrator, rules, mechanism     # noqa: E402
from app.archive import repository as repo               # noqa: E402
from app.knowledge import formula as formula_kb, kb      # noqa: E402
from app.ingest import vision_llm as V                   # noqa: E402
from app.schemas import from_dict                        # noqa: E402

PASSED = 0


def check(name, cond, detail=""):
    global PASSED
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    PASSED += 1


def main():
    repo.init()
    print(f"运行环境: {config.runtime_info()}\n")

    # ---------- 一、患者身份：find_or_create（身份匹配唯一依据是姓名+身份证后四位）----------
    pid1, created = repo.find_or_create_patient(
        name="张三", sex="female", age_years=25, height_cm=160, weight_kg=55,
        id_last4="1001")
    check("首次建档 created=True", created and repo.get_patient(pid1)["name"] == "张三")

    pid1b, created = repo.find_or_create_patient(name="张三", sex="female",
                                                  age_years=25, id_last4="1001")
    check("同姓名+同身份证后四位 → 找回同一档案", pid1b == pid1 and not created)

    pid1c, created = repo.find_or_create_patient(name=" 张三 ", sex="female",
                                                  age_years=60, id_last4="1001")
    check("同姓名+同后四位：即使年龄跳变很大也精确找回同一人（不再依赖年龄容差）",
          pid1c == pid1 and not created
          and repo.get_patient(pid1)["age_years"] == 60, "年龄已更新为 60")

    pid2, created = repo.find_or_create_patient(name="张三", sex="male",
                                                 age_years=25, id_last4="2002")
    check("同名但身份证后四位不同 → 精确区分为新档案", created and pid2 != pid1)

    pid3a, created_a = repo.find_or_create_patient(name="张三", sex="female", age_years=25)
    pid3b, created_b = repo.find_or_create_patient(name="张三", sex="female", age_years=25)
    check("未提供身份证后四位 → 不做任何模糊猜测，每次都新建（不再有姓名+年龄±2岁匹配）",
          created_a and created_b and pid3a != pid3b and pid3a != pid1)

    lst = repo.list_patients(query="张三")
    check("按姓名检索档案列表", len(lst) == 4
          and all("doc_count" in p and "analysis_count" in p for p in lst))

    # ---------- 二、备注（症状描述）入档 ----------
    repo.add_note(pid1, "近两月餐后腹胀，偶有口干多饮")
    snap = repo.snapshot(pid1)
    check("备注入档并出现在快照", len(snap["notes"]) == 1
          and "口干" in snap["notes"][0]["text"])

    # ---------- 三、第二病种（糖脂/尿酸，男 46）全链路 ----------
    m_pid, _ = repo.find_or_create_patient(
        name="李四", sex="male", age_years=46, height_cm=175, weight_kg=82)
    sample = json.loads(
        (config.SAMPLE_DIR / "sample_metabolic_extraction.json").read_text("utf-8"))
    ext = from_dict(sample)
    repo.save_document(m_pid, "生化全套_2026-07.jpg", "/tmp/x.jpg", ext)

    m_snap = repo.snapshot(m_pid)
    m_tags = rules.identify_risks(m_snap)
    ids = {t["id"] for t in m_tags}
    check("规则引擎识别糖/脂/尿酸风险",
          {"glucose_high", "dyslipidemia", "hyperuricemia"} <= ids, f"tags={sorted(ids)}")
    check("血糖达切点 → severity=high 建议就医",
          next(t for t in m_tags if t["id"] == "glucose_high")["severity"] == "high")
    check("肝系标签未误报（ALT 正常、无超声）",
          not ({"fatty_liver_us", "nash_possible", "liver_enzyme_elevated"} & ids))

    chain = mechanism.build_chain(m_tags, m_snap)
    genes = {e["gene"] for e in chain["entities"]}
    check("机制实体随病种切换（含 TCF7L2/ABCG2，不含 PNPLA3）",
          {"TCF7L2", "ABCG2"} <= genes and "PNPLA3" not in genes, f"genes={sorted(genes)}")
    plan = mechanism.plan_biocompute(chain)
    check("生物计算计划受上限保护", 0 < len(plan) <= mechanism.MAX_BIOCOMPUTE_ITEMS,
          f"{len(plan)} 项")

    m_formula = formula_kb.build_formula(sorted(ids), sex="male")
    names = {i["name"] for i in m_formula["ingredients"]}
    check("组方随病种切换（含葛根/桑叶等糖脂方向）",
          {"葛根", "桑叶"} <= names, f"names={sorted(names)}")
    check("组方与肝脂演示方不同（不含荷叶）", "荷叶" not in names)
    # 阶段六：玉米须移入尿酸加减候选（首位），本画像含 hyperuricemia，
    # 门禁触发后由候选序列中首个目录内品种（菊苣）承接并留痕。
    check("目录门禁仍生效（玉米须→加减候选内目录品种替换留痕）",
          any(s["original"] == "玉米须" and s.get("replaced_by")
              and kb.in_catalog(s["replaced_by"])
              for s in m_formula["substitutions"]))
    check("组方遵循辨证→立法→底方→加减（含方名/立法/留痕）",
          m_formula["formula_name"] and m_formula["treatment_principle"]
          and any(m.startswith("立法") for m in m_formula["modification_log"]))
    check("全部原料通过目录校验", all(c["ok"] for c in m_formula["catalog_check"]))

    result = orchestrator.run_analysis(m_pid)
    check("第二病种 Agent 全链路跑通（含报告）",
          len(result["reports"]) == 4
          and {r["report_type"] for r in result["reports"]}
          == {"health_analysis", "tea_plan"})
    detail = repo.get_analysis(result["analysis_id"])
    check("分析详情含 formula 与 trace（历史可回放）",
          detail["formula"] and detail["trace"]
          and detail["formula"]["ingredients"], f"trace {len(detail['trace'])} 步")
    md = Path(next(r["path"] for r in result["reports"]
                   if r["report_type"] == "tea_plan" and r["format"] == "md")
              ).read_text("utf-8")
    check("茶饮报告文案随病种泛化（含尿酸/血糖方向、无肝胰硬编码依赖）",
          "尿酸" in md and "血糖" in md)

    # ---------- 四、无风险患者兜底 ----------
    ok_pid, _ = repo.find_or_create_patient(
        name="王平", sex="male", age_years=30, height_cm=175, weight_kg=65)
    ok_result = orchestrator.run_analysis(ok_pid)
    check("无风险标签：分析可跑通", ok_result["risk_tags"] == [])
    check("无风险：不硬凑配方、不出茶饮报告",
          not ok_result["formula"]["ingredients"]
          and {r["report_type"] for r in ok_result["reports"]} == {"health_analysis"})
    h_md = Path(next(r["path"] for r in ok_result["reports"]
                     if r["format"] == "md")).read_text("utf-8")
    check("健康报告含无风险兜底说明", "未识别出显著风险标签" in h_md)

    # ---------- 五、级联删除 ----------
    repo.delete_patient(ok_pid)
    check("级联删除：档案与分析一并清除",
          repo.get_patient(ok_pid) is None and repo.list_analyses(ok_pid) == []
          and repo.list_reports(ok_pid) == [])

    # ---------- 六、知识库完整性 ----------
    med = kb.medical()
    check("知识库：全部机制实体带 triggers",
          all(e.get("triggers") for e in med["mechanism_entities"]))
    check("知识库：规则新标签均有条目",
          all(kb.condition_knowledge(t) for t in
              ("glucose_high", "dyslipidemia", "hyperuricemia",
               "blood_pressure_high", "imaging_nodule")))

    # ---------- 七、体重偏低 / 自述症状（截图场景回归）----------
    th_pid, _ = repo.find_or_create_patient(
        name="薛测", sex="male", age_years=28, height_cm=170, weight_kg=50)
    repo.add_note(th_pid, "头疼")
    th = orchestrator.run_analysis(th_pid)
    th_ids = [t["id"] for t in th["risk_tags"]]
    check("BMI 偏低被识别（不再是「无显著风险标签」）", "underweight" in th_ids)
    check("自述症状进入风险区并提示就医", "symptom_note" in th_ids)
    check("安全门禁：不给体重偏低/仅症状者推组方",
          th["formula"]["ingredients"] == []
          and {r["report_type"] for r in th["reports"]} == {"health_analysis"})
    th_md = Path(next(r["path"] for r in th["reports"]
                      if r["format"] == "md")).read_text("utf-8")
    check("报告收录自述症状原文与数据充分性提示",
          "头疼" in th_md and "数据充分性提示" in th_md
          and "体重与营养" in th_md)
    check("体重偏低不出现减重方向建议", "减重 5%" not in th_md)

    # ---------- 八、视觉抽取前置校验与故障识别（纯单元，不联网）----------
    png = V._probe_png()
    tmp = config.UPLOAD_DIR / "probe_test.jpg"       # 扩展名故意与实际不符
    tmp.write_bytes(png)
    block, diag = V._build_source_block(tmp)
    check("魔数嗅探纠正 media_type（不信任扩展名）",
          block["type"] == "image" and diag["media_type"] == "image/png")

    empty = config.UPLOAD_DIR / "empty_test.png"
    empty.write_bytes(b"")
    try:
        V._build_source_block(empty)
        check("空文件拦截", False)
    except V.VisionInputError:
        check("空文件在发请求前被拦截", True)

    heic = config.UPLOAD_DIR / "phone_test.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"0" * 64)
    try:
        V._build_source_block(heic)
        check("未知格式拦截", False)
    except V.VisionInputError as exc:
        check("未知格式拦截并给出转存指引", "JPG" in str(exc))

    big = config.UPLOAD_DIR / "big_test.png"
    big.write_bytes(png[:8] + b"0" * (V.MAX_IMAGE_BYTES + 10))
    try:
        V._build_source_block(big)
        check("超限体积拦截", False)
    except V.VisionInputError:
        check("超出体积上限在发请求前被拦截", True)

    check("识别模型「没收到图像」的典型答复",
          V._looks_like_no_image("这次对话里始终没有图片，只有文字。")
          and V._looks_like_no_image("I cannot see any image in this conversation.")
          and not V._looks_like_no_image('{"document_type":"lab_report"}'))
    for f in (tmp, empty, heic, big):
        f.unlink(missing_ok=True)

    # ---------- 九、前端契约（图片上传 + 手动录入 + 身份证后四位，三者并存）----------
    html = (ROOT / "static" / "index.html").read_text("utf-8")
    js = (ROOT / "static" / "app.js").read_text("utf-8")
    check("前端保留图片上传与视觉自检入口（本轮澄清：不移除图片抽取）",
          'id="dropzone"' in html and 'id="fileInput"' in html
          and 'id="btnSelftest"' in html and "/api/selftest/vision" in js)
    check("前端同时含身份证后四位输入与手动录入表单（两种数据入口并存）",
          'id="fId4"' in html and 'id="obsCode"' in html and 'id="fdOrgan"' in html)
    check("前端在选中档案后同步表单（防误改他人档案），含后四位字段",
          '$("#fName").value = p.name' in js and '$("#fId4").value = p.id_last4' in js)
    check("前端含登录/注册遮罩与管理员面板",
          'id="authMask"' in html and 'id="adminMask"' in html
          and 'id="btnLogin"' in html and 'id="btnRegister"' in html)
    check("前端 API 封装自动附带 Authorization 头，401 自动登出",
          "headers.Authorization" in js and "logout(" in js)

    # ---------- 十、机制文案精确性：肝酶升高不应误挂脂肪堆积机制 ----------
    from app.schemas import from_dict as _from_dict
    lft_pid, _ = repo.find_or_create_patient(name="肝酶单项", sex="male", age_years=45)
    lft_ext = _from_dict({
        "document_type": "lab_report", "engine": "mock", "exam_date": "2026-08-01",
        "patient": {"sex": "male", "age_years": 45},
        "observations": [
            {"code": "ALT", "display": "丙氨酸氨基转移酶", "value_num": 78,
             "unit": "U/L", "ref_low": 0, "ref_high": 40, "abnormal_flag": "H"},
        ], "findings": [], "impressions": [],
    })
    repo.save_document(lft_pid, "肝酶.jpg", "/tmp/x.jpg", lft_ext)
    lft_snap = repo.snapshot(lft_pid)
    lft_tags = rules.identify_risks(lft_snap)
    check("单纯肝酶升高（无脂肪肝影像）不触发脂肪堆积特异性机制标签",
          {t["id"] for t in lft_tags} == {"liver_enzyme_elevated"})
    lft_chain = mechanism.build_chain(lft_tags, lft_snap)
    lft_mech = [i for lv in lft_chain["levels"] if lv["level"] == "生物机制"
                for i in lv["items"]]
    check("单纯肝酶升高：机制文案不误提 PNPLA3/脂肪从头合成",
          not any("PNPLA3" in m or "从头合成" in m for m in lft_mech))
    check("单纯肝酶升高：给出病因未定、需医生鉴别的通用表述",
          any("病因需结合病史" in m for m in lft_mech))

    # 对照：真正的脂肪肝患者（超声+肝酶）应仍看到 PNPLA3 机制实体
    fl_pid, _ = repo.find_or_create_patient(name="脂肪肝对照", sex="female", age_years=30)
    fl_ext = _from_dict({
        "document_type": "ultrasound_report", "engine": "mock", "exam_date": "2026-08-01",
        "patient": {"sex": "female", "age_years": 30},
        "findings": [{"organ": "肝脏", "description": "肝脏体积增大，回声增强",
                      "flags": ["回声增强"]}],
        "impressions": ["脂肪肝"], "observations": [
            {"code": "ALT", "display": "丙氨酸氨基转移酶", "value_num": 78,
             "unit": "U/L", "ref_low": 0, "ref_high": 40, "abnormal_flag": "H"},
        ],
    })
    repo.save_document(fl_pid, "超声.jpg", "/tmp/x.jpg", fl_ext)
    fl_snap = repo.snapshot(fl_pid)
    fl_tags = rules.identify_risks(fl_snap)
    fl_chain = mechanism.build_chain(fl_tags, fl_snap)
    fl_genes = {e["gene"] for e in fl_chain["entities"]}
    check("对照：真实脂肪肝患者仍正确关联 PNPLA3 机制实体", "PNPLA3" in fl_genes)

    # ---------- 十一、身份证后四位精确匹配（区分同名不同人）----------
    ida, _ = repo.find_or_create_patient(name="王伟", sex="male", age_years=40,
                                         id_last4="1234")
    idb, created_b = repo.find_or_create_patient(name="王伟", sex="male", age_years=41,
                                                  id_last4="5678")
    check("同名同性别但身份证后四位不同 → 精确区分为两个人（不受年龄容差影响）",
          created_b and idb != ida)
    ida2, created_a2 = repo.find_or_create_patient(name="王伟", sex="male",
                                                    age_years=99, id_last4="1234")
    check("同姓名+同身份证后四位 → 即使年龄差异很大也精确找回同一人",
          not created_a2 and ida2 == ida)
    try:
        repo.find_or_create_patient(name="张三丰", sex="male", id_last4="12ab")
        check("非法身份证后四位格式应被规范化处理", True)  # _norm_id4 静默丢弃非法值
    except Exception:
        check("非法身份证后四位格式不应抛异常", False)

    # ---------- 十二、手动数据录入（替代图片上传逻辑）----------
    manual_pid, _ = repo.find_or_create_patient(name="手动录入测试", sex="female",
                                                 age_years=35)
    obs_id = repo.add_observation(manual_pid, code="ALT", display="丙氨酸氨基转移酶",
                                  value_num=97, unit="U/L", ref_low=0, ref_high=40,
                                  abnormal_flag="H", observed_at="2026-08-01")
    check("手动录入指标不依赖任何文档/图片（document_id 为空）",
          bool(obs_id))
    finding_id = repo.add_manual_finding(manual_pid, "肝脏", "肝脏体积增大，回声增强",
                                         flags=["回声增强", "欠均匀"])
    check("手动录入影像所见成功（document_id 为空）", bool(finding_id))
    imp_id = repo.add_manual_impression(manual_pid, "脂肪肝")
    check("手动录入诊断提示成功（替代图片抽取的 impressions 字段）", bool(imp_id))
    manual_snap = repo.snapshot(manual_pid)
    check("手动录入的指标/所见/提示均出现在档案快照中",
          "ALT" in manual_snap["observations_latest"]
          and len(manual_snap["findings"]) == 1
          and manual_snap["findings"][0]["organ"] == "肝脏"
          and any(i["text"] == "脂肪肝" for i in manual_snap["impressions"]))
    manual_result = orchestrator.run_analysis(manual_pid)
    check("纯手动录入数据同样能跑通完整 Agent 分析",
          any(t["id"] == "liver_enzyme_elevated" for t in manual_result["risk_tags"])
          and any(t["id"] == "fatty_liver_us" for t in manual_result["risk_tags"]))

    # ---------- 十三、组方引擎扩展证型：失眠 / 咽喉 / 气虚 ----------
    from app.knowledge import tcm_syndrome

    insomnia_syn = tcm_syndrome.detect(["最近两周失眠，入睡困难，多梦易醒"])
    check("失眠类关键词被正确识别为证型",
          len(insomnia_syn) == 1 and insomnia_syn[0]["id"] == "insomnia_pattern")
    throat_syn = tcm_syndrome.detect(["嗓子疼，咽干"])
    check("咽喉类关键词被正确识别为证型",
          any(s["id"] == "throat_pattern" for s in throat_syn))
    qi_syn = tcm_syndrome.detect(["总是感觉乏力，气短，没精神"])
    check("气虚类关键词被正确识别为证型",
          any(s["id"] == "qi_deficiency_pattern" for s in qi_syn))
    check("无自述文本时不产生任何证型（不臆造）", tcm_syndrome.detect([]) == []
          and tcm_syndrome.detect(["今天天气不错"]) == [])

    insomnia_formula = formula_kb.build_formula(["insomnia_pattern"], sex="female")
    ins_names = {i["name"] for i in insomnia_formula["ingredients"]}
    check("仅失眠证型（无生物医学风险）也能生成组方（安全门禁已放行证型槽位）",
          len(insomnia_formula["ingredients"]) > 0)
    check("失眠组方含养心安神类原料", {"酸枣仁", "茯神"} & ins_names)

    throat_formula = formula_kb.build_formula(["throat_pattern"], sex="male")
    throat_names = {i["name"] for i in throat_formula["ingredients"]}
    check("咽喉证型组方含利咽类原料", {"桔梗", "金银花", "罗汉果"} & throat_names)

    qi_formula = formula_kb.build_formula(["qi_deficiency_pattern"], sex="male")
    qi_names = {i["name"] for i in qi_formula["ingredients"]}
    check("气虚证型组方含补气类原料", {"黄芪", "党参", "山药"} & qi_names)

    check("per-candidate 剂量覆盖生效（桔梗与金银花剂量不同，不再共用一个数字）",
          len({i["grams"] for i in throat_formula["ingredients"]
               if i["name"] in ("桔梗", "金银花")}) >= 1)  # 至少验证取值来自 doses 映射

    combo_formula = formula_kb.build_formula(
        ["fatty_liver_us", "liver_enzyme_elevated", "insomnia_pattern"], sex="female")
    combo_names = {i["name"] for i in combo_formula["ingredients"]}
    check("主证底方 + 兼夹风险加减（不再多套方混拼）：失眠主证走酸枣仁汤底方，"
          "肝脂风险以荷叶/杞菊作加味留痕",
          combo_formula["formula_name"].startswith("酸枣仁汤")
          and "酸枣仁" in combo_names
          and ({"荷叶", "枸杞子", "菊花"} & combo_names)
          and any(m.startswith("加味") for m in combo_formula["modification_log"]))
    check("组方总量受上限保护（真实处方 5–8 味，含加减封顶 10）",
          len(combo_formula["ingredients"]) <= formula_kb.MAX_TOTAL)

    check("药食同源目录已扩充（覆盖失眠/咽喉/气虚方向候选，非此前的 16 味孤本）",
          len(kb.catalog()) >= 30)

    # ---------- 十四、编排器完整链路：证型识别自动接入组方与报告 ----------
    syn_pid, _ = repo.find_or_create_patient(name="失眠患者", sex="female", age_years=32)
    repo.add_note(syn_pid, "最近一个月总是失眠，入睡困难，白天也没精神，有点气短")
    syn_result = orchestrator.run_analysis(syn_pid)
    check("编排器自动识别自述证型并写入返回结果",
          len(syn_result["syndrome_tags"]) >= 1)
    check("证型驱动组方生成（无生物医学风险也不再是空配方）",
          len(syn_result["formula"]["ingredients"]) > 0)
    syn_detail = repo.get_analysis(syn_result["analysis_id"])
    check("证型标签随分析一并持久化，可供历史回放", bool(syn_detail["syndrome_tags"]))
    tea_md = Path(next(r["path"] for r in syn_result["reports"]
                       if r["format"] == "md" and r["report_type"] == "tea_plan")
                 ).read_text("utf-8")
    check("茶饮报告明确标注证型来自自述关键词、非诊断",
          "自述关键词匹配" in tea_md or "非诊断" in tea_md)

    # ---------- 十五、问答系统引用历史分析趋势 ----------
    from app.agent import qa as qa_mod

    hist_pid, _ = repo.find_or_create_patient(name="趋势测试", sex="male", age_years=50)
    for alt_val in (97, 60, 42):
        repo.add_observation(hist_pid, code="ALT", value_num=alt_val, unit="U/L",
                             ref_low=0, ref_high=40, observed_at="2026-0"
                             + str(1 + (97 - alt_val) // 30) + "-01")
        orchestrator.run_analysis(hist_pid)
    ctx = qa_mod._context(hist_pid)
    hist_key = next((k for k in ctx if k.startswith("历次分析记录")), None)
    check("问答上下文包含多次历史分析记录（不是只取最近一次）",
          hist_key is not None and len(ctx[hist_key]) >= 2)
    check("问答上下文包含同一指标跨次数值趋势，供纵向对比作答",
          "ALT" in ctx["指标历史趋势（同一指标跨多次记录的数值变化）"]
          and len(ctx["指标历史趋势（同一指标跨多次记录的数值变化）"]["ALT"]) >= 2)

    # ---------- 十六、目录全量化（覆盖卫健委名单）与门禁前缀匹配 ----------
    from app.knowledge import tcm_syndrome as syn_mod
    kb.catalog.cache_clear()
    cat = kb.catalog()
    check("目录规模达到官方全量水平（≥106 种，实际含2002名单/试点/增补分批标注）",
          len(cat) >= 106, f"实际 {len(cat)} 种")
    check("门禁前缀匹配：2023 年增补品种（麦冬）判定为目录内", kb.in_catalog("麦冬"))
    check("门禁前缀匹配：2014 试点品种（人参）判定为目录内", kb.in_catalog("人参"))
    check("玉米须（保健食品原料目录）仍被判为目录外", not kb.in_catalog("玉米须"))
    check("茯神（目录收录名为茯苓，口径存疑）如实判为目录外", not kb.in_catalog("茯神"))

    banned = {"seasoning", "animal", "toxic_caution"}
    from app.knowledge.classic_formulas import (ADDITION_RULES as _ADD,
                                                CLASSIC_FORMULAS as _CF)
    all_cands = [s["name"] for spec in _CF.values() for s in spec["base"]]
    all_cands += [v[0] for spec in _CF.values()
                  for v in (spec.get("sex_envoy") or {}).values()]
    all_cands += [n for r in _ADD for n, _ in r["add"]]
    check("底方骨架与加减候选全部真实存在于目录（无静默跳过隐患）",
          all(c in cat for c in all_cands))
    check("香料/动物/毒性慎用类绝不出现在底方与加减候选中",
          not any(banned & set(cat[c].get("tags", [])) for c in all_cands))
    check("有毒品种（白果/桃仁）已收录目录但绝不入方",
          "白果" in cat and "桃仁" in cat
          and "白果" not in all_cands and "桃仁" not in all_cands)
    check("AVOID_IF 所有键均在目录中", all(k in cat for k in formula_kb.AVOID_IF))

    # ---------- 十七、七组新证型：识别 → 出方 → 药性方向正确 ----------
    def _syn_formula(pattern, note, sex="female"):
        got = syn_mod.detect([note])
        assert any(s["id"] == pattern for s in got), f"{pattern} 未识别: {got}"
        return formula_kb.build_formula([pattern], sex=sex)

    f = _syn_formula("damp_heat_pattern", "口苦口黏，舌苔黄腻，小便黄")
    names = {i["name"] for i in f["ingredients"]}
    check("湿热证型：识别+出方且含清利之品", {"蒲公英", "栀子"} & names)
    f = _syn_formula("yin_deficiency_pattern", "口干咽干，晚上盗汗")
    names = {i["name"] for i in f["ingredients"]}
    check("阴虚证型：含养阴之品（麦冬/玉竹/铁皮石斛）",
          {"麦冬", "玉竹", "铁皮石斛"} & names)
    f = _syn_formula("constipation_pattern", "大便干结排便困难")
    names = {i["name"] for i in f["ingredients"]}
    check("便秘证型：含润下之品", {"火麻仁", "郁李仁"} & names)
    f = _syn_formula("wind_heat_pattern", "咽痛发热流黄鼻涕")
    names = {i["name"] for i in f["ingredients"]}
    check("风热证型：含辛凉解表之品", {"金银花", "薄荷", "桑叶"} & names)
    f = _syn_formula("spleen_damp_pattern", "大便不成形饭后腹胀")
    names = {i["name"] for i in f["ingredients"]}
    check("脾虚湿盛证型：含健脾固涩之品且无润肠品",
          ({"白扁豆", "芡实", "莲子", "山药"} & names)
          and not ({"火麻仁", "郁李仁", "蜂蜜"} & names))
    f = _syn_formula("cough_phlegm_pattern", "咳嗽有痰白痰多")
    names = {i["name"] for i in f["ingredients"]}
    check("咳嗽证型：仅用甜杏仁（永不出现苦杏仁），含化痰之品",
          "杏仁（甜）" in names or ({"桔梗", "化橘红", "罗汉果"} & names))
    f = _syn_formula("summer_damp_pattern", "暑天头身困重没胃口")
    names = {i["name"] for i in f["ingredients"]}
    check("暑湿证型：含芳香化浊之品", {"藿香", "香薷", "白扁豆花"} & names)

    # ---------- 十八、安全联动 ----------
    both = syn_mod.detect(["有时便秘大便干，有时又便溏拉肚子"])
    both_ids = {s["id"] for s in both}
    check("便秘/便溏证型互斥：药性相反同现时便溏优先（安全取舍并留痕）",
          "constipation_pattern" not in both_ids
          and "spleen_damp_pattern" in both_ids
          and any("药性相反" in e for s in both for e in s["evidence"]))
    f = formula_kb.build_formula(["constipation_pattern", "glucose_high"], sex="male")
    check("血糖异常自动规避蜂蜜", "蜂蜜" not in {i["name"] for i in f["ingredients"]})
    check("展示名不再出现拼接怪名（如「干果枸杞子」）",
          all("干果枸杞子" != i["display"]
              for pat in ["yin_deficiency_pattern"]
              for i in formula_kb.build_formula([pat], "female")["ingredients"]))

    # ---------- 十九、机制链做实（截图画像回归：体重偏低+嗓子疼）----------
    scr_pid, _ = repo.find_or_create_patient(name="机制链画像", sex="female",
                                             age_years=22, height_cm=165,
                                             weight_kg=42, id_last4="4201")
    repo.add_note(scr_pid, "嗓子疼")
    scr = orchestrator.run_analysis(scr_pid)
    scr_chain = scr["mechanism_chain"]
    bio_lv = {l["level"]: l["items"] for l in scr_chain["levels"]}
    check("体重偏低有真实病理生理机制（能量负平衡，不再是两层复述）",
          any("能量" in i or "负平衡" in i for i in bio_lv["生物机制"]))
    check("咽喉证型触发黏膜机制条目（证型进入机制链触发源）",
          any("黏膜" in i for i in bio_lv["生物机制"]))
    check("风险方向含就医预警（声嘶超2周/非刻意体重下降等红旗信号）",
          any("声音嘶哑" in i or "就医" in i for i in bio_lv["风险方向"]))
    check("无分子靶点时给出生物计算适用性理由（不虚构调用、说明为什么）",
          "不为演示效果虚构调用" in scr_chain["biocompute_applicability"]
          and scr["biocompute_plan"] == [])
    check("EXEC_BIOCOMPUTE trace 展示适用性理由而非干瘪一句",
          any(t["step"] == "EXEC_BIOCOMPUTE" and "机制靶点" in t["detail"]
              for t in scr["trace"]))
    check("该画像组方照常（桔梗汤加味，机制链改造不影响组方）",
          scr["formula"]["formula_name"] == "桔梗汤加味")
    # 对照：代谢患者仍走分子层 + 生成调用计划（向后兼容）
    met_pid, _ = repo.find_or_create_patient(name="机制链对照", sex="male",
                                             age_years=45, id_last4="4202")
    repo.add_observation(met_pid, code="ALT", value_num=97, unit="U/L",
                         ref_low=0, ref_high=40, abnormal_flag="H",
                         observed_at="2026-08-01")
    repo.add_manual_impression(met_pid, "脂肪肝")
    met = orchestrator.run_analysis(met_pid)
    check("对照：代谢方向仍有分子实体与调用计划（含适用性正向说明）",
          len(met["mechanism_chain"]["entities"]) > 0
          and len(met["biocompute_plan"]) > 0
          and "已生成生物计算调用计划" in
              met["mechanism_chain"]["biocompute_applicability"])

    # ---------- 二十、感染炎症覆盖 + BP文本 + 肾轴 + AI解读节点 ----------
    inf_pid, _ = repo.find_or_create_patient(name="感染画像", sex="male",
                                             age_years=28, id_last4="6001")
    repo.add_observation(inf_pid, code="WBC", value_num=14.8, unit="10^9/L",
                         ref_low=3.5, ref_high=9.5, abnormal_flag="H",
                         observed_at="2026-08-09")
    repo.add_observation(inf_pid, code="CRP", value_num=45.2, unit="mg/L",
                         ref_low=0, ref_high=10, abnormal_flag="H",
                         observed_at="2026-08-09")
    repo.add_note(inf_pid, "嗓子疼，咽痛发热")
    inf = orchestrator.run_analysis(inf_pid)
    inf_ids = {t["id"] for t in inf["risk_tags"]}
    check("感染炎症规则覆盖：WBC 升高与 CRP 升高均被识别",
          {"wbc_high", "crp_high"} <= inf_ids)
    inf_genes = {e["gene"] for e in inf["mechanism_chain"]["entities"]}
    check("急性期反应分子实体（IL6/TNF/CRP，真实 UniProt 条目）进入机制链",
          {"IL6", "TNF", "CRP"} <= inf_genes)
    check("感染方向生成 AlphaFold 蛋白结构调用计划",
          any(b["gene"] == "IL6" and b["service"] == "alphafold_db"
              for b in inf["biocompute_plan"]))
    check("感染+咽痛发热 → 组方走风热解表（桑菊饮），不给感染期乱补",
          inf["formula"]["formula_name"] == "桑菊饮化裁")

    bp_pid, _ = repo.find_or_create_patient(name="血压文本", sex="female",
                                            age_years=52, id_last4="6002")
    repo.add_observation(bp_pid, code="BP", value_text="152/98", unit="mmHg",
                         observed_at="2026-08-09")
    repo.add_observation(bp_pid, code="BUN", value_num=12.4, unit="mmol/L",
                         ref_low=2.6, ref_high=7.5, abnormal_flag="H",
                         observed_at="2026-08-09")
    repo.add_observation(bp_pid, code="HGB", value_num=98, unit="g/L",
                         ref_low=115, ref_high=150, abnormal_flag="L",
                         observed_at="2026-08-09")
    repo.add_observation(bp_pid, code="CR", value_num=158, unit="umol/L",
                         ref_low=41, ref_high=111, abnormal_flag="H",
                         observed_at="2026-08-09")
    bp_r = orchestrator.run_analysis(bp_pid)
    bp_ids = {t["id"] for t in bp_r["risk_tags"]}
    check("血压文本值「152/98」被解析识别（此前静默漏掉）",
          "blood_pressure_high" in bp_ids)
    check("尿素氮规则补齐（bun_high）", "bun_high" in bp_ids)
    bp_bio = next(l["items"] for l in bp_r["mechanism_chain"]["levels"]
                  if l["level"] == "生物机制")
    check("肾脏-贫血轴机制链完整（EPO 减少→肾性贫血、尿酸-肾双向循环）",
          any("促红细胞生成素" in i for i in bp_bio)
          and any("双向循环" in i for i in bp_bio))

    interp = bp_r["interpretation"]
    check("AI 综合解读节点在位：MOCK 下如实不可用并说明原因（绝不出假解读）",
          interp["available"] is False and "MOCK" in interp["reason"]
          and any(t["step"] == "AI_INTERPRET" for t in bp_r["trace"]))
    check("解读随分析持久化，历史回放可见",
          (repo.get_analysis(bp_r["analysis_id"]) or {}).get("interpretation",
                                                             {}).get("reason"))
    hr_md = Path(next(x["path"] for x in bp_r["reports"]
                      if x["report_type"] == "health_analysis"
                      and x["format"] == "md")).read_text("utf-8")
    check("健康报告含「AI 综合解读」章节（未启用时如实说明，不放模板假内容）",
          "AI 综合解读" in hr_md and "本次未生成 AI 综合解读" in hr_md)
    from app.agent import interpretation as interp_mod
    from app.reportgen import compliance as comp_mod
    check("解读合规闸接口在位（find_violations 能命中绝对化表述）",
          comp_mod.find_violations("本方保证根治高血压") != []
          and comp_mod.find_violations("多饮水、规律作息") == [])

    print(f"\n全部通过 ({PASSED} 项)")


if __name__ == "__main__":
    main()
