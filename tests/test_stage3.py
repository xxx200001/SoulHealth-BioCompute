"""阶段三离线测试：Agent 全链路 + 合规红线回归 + docx 结构校验（零依赖零网络）。

运行：python tests/test_stage3.py
"""
import json
import os
import sys
import xml.dom.minidom
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SOULHEALTH_MOCK"] = "1"

from app import config                                  # noqa: E402
from app.agent import orchestrator                      # noqa: E402
from app.knowledge import kb                             # noqa: E402
from app.archive import repository as repo              # noqa: E402
from app.ingest.pipeline import ingest_document         # noqa: E402
from app.knowledge import kb                            # noqa: E402
from app.reportgen import compliance                    # noqa: E402

PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    PASSED += 1


def main() -> None:
    repo.init()
    # 准备演示患者档案（复用阶段二 MOCK 摄取）
    pid = repo.create_patient(sex="female", age_years=25, height_cm=163, weight_kg=83)
    for fname in ("demo_ultrasound.jpg", "demo_肝功化验.jpg"):
        f = config.UPLOAD_DIR / fname
        f.write_bytes(b"\xff\xd8\xff\xe0demo")
        ingest_document(pid, f)

    # ---- 知识库目录校验
    chk = {c["name"]: c for c in kb.catalog_check(["山楂", "玉米须", "冬瓜皮"])}
    check("目录校验：山楂通过 / 玉米须标出 / 未知原料要求人工核对",
          chk["山楂"]["ok"] and not chk["玉米须"]["ok"]
          and chk["玉米须"]["status"] == "保健食品原料目录"
          and not chk["冬瓜皮"]["ok"])

    # ---- Agent 全链路
    result = orchestrator.run_analysis(pid)
    tag_ids = {t["id"] for t in result["risk_tags"]}
    check("风险识别：五类标签齐备",
          {"obesity", "fatty_liver_us", "liver_enzyme_elevated",
           "nash_possible", "pancreatic_steatosis_possible",
           "insulin_resistance_risk"} <= tag_ids,
          "；".join(sorted(tag_ids)))
    obesity = next(t for t in result["risk_tags"] if t["id"] == "obesity")
    check("BMI 分级：31.24 → 中度", "中度" in obesity["label"], obesity["label"])
    enzyme = next(t for t in result["risk_tags"] if t["id"] == "liver_enzyme_elevated")
    check("ALT 超上限2倍 → severity=high", enzyme["severity"] == "high",
          "；".join(enzyme["evidence"]))

    # ---- 配方与目录门禁
    names = [i["name"] for i in result["formula"]["ingredients"]]
    # 阶段六重构：组方遵循 辨证→立法→底方→加减，肝脂代谢方向以《丹溪心法》
    # 保和丸化裁为底方（山楂/茯苓/陈皮/莱菔子/麦芽骨架 + 女用玫瑰花使药），
    # 再按兼夹风险加减（荷叶化浊、桑叶葛根清降、杞菊养肝），总量封顶 10 味。
    fm = result["formula"]
    check("底方骨架完整（保和丸化裁：山楂君、茯苓陈皮臣、消导佐、女用玫瑰使）",
          fm["formula_name"] == "保和丸化裁·消导化浊方"
          and {"山楂", "茯苓", "陈皮", "莱菔子", "麦芽", "玫瑰花"} <= set(names)
          and names[0] == "山楂" and len(names) <= 10,
          "、".join(names))
    check("加减有据可循（荷叶化浊/杞菊养肝等留痕于 modification_log）",
          "荷叶" in names
          and any("荷叶" in m for m in fm["modification_log"])
          and any(m.startswith("立法") for m in fm["modification_log"]))
    subs = fm["substitutions"]
    check("玉米须门禁演示链路保留（本画像无尿酸标签故不触发替换，subs 为空合理）",
          subs == [] or all("replaced_by" in s for s in subs)
          and subs[0]["replaced_by"] == "赤小豆")
    check("全部原料通过目录校验",
          all(c["ok"] for c in result["formula"]["catalog_check"]))
    doses = {i["name"]: i["grams"] for i in result["formula"]["ingredients"]}
    check("剂量为底方与加减规则定义值且均落在目录安全区间内",
          doses["山楂"] == 8 and doses["茯苓"] == 8 and doses["陈皮"] == 4
          and all(  # 每味剂量都不超出目录 dose_g 上限（安全区间）
              doses[n] <= (kb.get_ingredient(n)["dose_g"][1]
                           if isinstance(kb.get_ingredient(n)["dose_g"][1], (int, float))
                           else 99)
              for n in names))

    # ---- 机制链与生物计算计划
    chain = result["mechanism_chain"]
    genes = {e["gene"] for e in chain["entities"]}
    check("机制实体含 PNPLA3(Q9NST1)/TM6SF2/GCKR/ADIPOQ",
          {"PNPLA3", "TM6SF2", "GCKR", "ADIPOQ"} <= genes
          and any(e.get("uniprot") == "Q9NST1" for e in chain["entities"]))
    check("机制链四层齐备", [l["level"] for l in chain["levels"]] ==
          ["临床数据", "基因 / 蛋白", "生物机制", "风险方向"])
    services = sorted({b["service"] for b in result["biocompute_plan"]})
    statuses = [b.get("status") for b in result["biocompute_plan"]]
    check("生物计算：alphafold_db + evo2 已执行（MOCK 缓存）",
          services == ["alphafold_db", "evo2"] and statuses.count("done") == 5
          and statuses.count("pending_resolution") == 1
          and all(b.get("source") == "mock_cache" for b in result["biocompute_plan"]),
          f"done=5 pending=1（TM6SF2 待在线解析）")

    # ---- 报告产物与合规红线
    check("产出 4 个报告文件（2 文档 × docx/md）", len(result["reports"]) == 4)
    md_texts = {}
    for r in result["reports"]:
        check(f"文件存在：{Path(r['path']).name}", Path(r["path"]).exists())
        if r["format"] == "md":
            md_texts[r["report_type"]] = Path(r["path"]).read_text(encoding="utf-8")

    for rtype, text in md_texts.items():
        check(f"合规红线：{rtype} 无违禁话术", not compliance.lint(text))
        check(f"必备要素：{rtype} 含不替代声明与随访提示",
              not compliance.missing_required(text))
    tea_md = md_texts["tea_plan"]
    check("代茶饮文档：含方名/立法/化裁留痕与目录校验附表",
          "保和丸化裁" in tea_md and "治则" in tea_md
          and "加减与化裁依据" in tea_md and "校验结论" in tea_md)
    check("代茶饮文档：不含玉米须入方（本画像无尿酸标签，玉米须不参与）",
          "玉米须6g" not in tea_md and "玉米须 6g" not in tea_md)
    health_md = md_texts["health_analysis"]
    check("健康报告：四段式齐备",
          all(k in health_md for k in ["健康风险分析", "机制解释", "生物计算辅助分析",
                                        "健康管理建议", "免责声明"]))

    # ---- 合规闸负样本：违禁文案必须被拦截
    bad = "本方速效减脂，28天ALT下降50%，无任何副作用。"
    check("合规闸负样本：违禁话术全部命中", len(compliance.lint(bad)) >= 3,
          f"命中 {len(compliance.lint(bad))} 处")

    # ---- docx 结构校验（zip 完整 + XML 良构 + 内容在场）
    docx_paths = [r["path"] for r in result["reports"] if r["format"] == "docx"]
    for path in docx_paths:
        with zipfile.ZipFile(path) as zf:
            check(f"docx zip 无损坏：{Path(path).name}", zf.testzip() is None)
            names_in = set(zf.namelist())
            need = {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
                    "word/styles.xml", "docProps/core.xml"}
            check(f"docx 部件齐备：{Path(path).name}", need <= names_in)
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            xml.dom.minidom.parseString(doc_xml)        # 良构性
            xml.dom.minidom.parseString(zf.read("word/styles.xml").decode("utf-8"))
    tea_docx = next(p for p in docx_paths if "tea_plan" in p)
    with zipfile.ZipFile(tea_docx) as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
    check("docx 内容在场：配方、目录校验、就医提示",
          all(k in doc_xml for k in ["炒山楂", "荷叶", "目录校验", "不替代诊疗"]))

    # ---- 落库
    a = repo.get_analysis(result["analysis_id"])
    check("analyses 落库（快照/标签/机制/计划）",
          a is not None and a["status"] == "done"
          and len(a["risk_tags"]) == len(result["risk_tags"])
          and a["input_snapshot"]["patient"]["id"] == pid)
    check("reports 落库 4 行", len(repo.list_reports(pid, result["analysis_id"])) == 4)
    check("trace 七节点齐备（阶段六加 AI_INTERPRET）",
          [s["step"] for s in result["trace"]] ==
          ["LOAD_SNAPSHOT", "IDENTIFY_RISKS", "MATCH_KNOWLEDGE",
           "PLAN_BIOCOMPUTE", "EXEC_BIOCOMPUTE", "AI_INTERPRET",
           "GENERATE_REPORTS"])

    print(f"\n全部 {PASSED} 项通过 ✔")
    print("报告目录：", config.REPORT_DIR)
    print(json.dumps(result["trace"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
