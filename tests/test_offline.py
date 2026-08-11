"""离线冒烟测试：零三方依赖、零网络，验证阶段二核心闭环。

覆盖：建档 → BMI 派生 → 彩超摄取(MOCK) → 化验单摄取(MOCK) → 时间线 →
档案快照 → 脱敏正则 → 规则引擎否定词处理 → schema 严格校验。

运行：python tests/test_offline.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SOULHEALTH_MOCK"] = "1"  # 强制 MOCK，确保离线

from app import config                                   # noqa: E402
from app.archive import repository as repo               # noqa: E402
from app.ingest import deid                              # noqa: E402
from app.ingest.ocr_fallback import extract_with_rules   # noqa: E402
from app.ingest.pipeline import ingest_document          # noqa: E402
from app.schemas import from_dict                        # noqa: E402

PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    PASSED += 1


def main() -> None:
    print(f"运行环境: {config.runtime_info()}\n")
    repo.init()

    # 1. 建档（样例患者：女，25岁，163cm/83kg）
    pid = repo.create_patient(sex="female", age_years=25, height_cm=163, weight_kg=83)
    p = repo.get_patient(pid)
    check("建档成功", p is not None and p["sex"] == "female")

    bmi_series = repo.get_timeline(pid, "BMI")
    check("BMI 自动派生", len(bmi_series) == 1
          and abs(bmi_series[0]["value_num"] - 31.24) < 0.01
          and bmi_series[0]["abnormal_flag"] == "H",
          f"BMI={bmi_series[0]['value_num']}")

    # 2. 彩超摄取（MOCK：任意图片名 → 彩超样例）
    us_file = config.UPLOAD_DIR / "demo_ultrasound.jpg"
    us_file.write_bytes(b"\xff\xd8\xff\xe0demo")  # 占位字节即可，MOCK 不读像素
    r1 = ingest_document(pid, us_file)
    ext1 = r1["extraction"]
    check("彩超摄取（MOCK）", r1["engine"] == "mock"
          and "脂肪肝" in ext1["impressions"]
          and "胰腺改变请结合临床" in ext1["impressions"])
    pancreas = [f for f in ext1["findings"] if f["organ"] == "胰腺"]
    check("胰腺异常 flags 抽取", bool(pancreas) and "回声欠均匀" in pancreas[0]["flags"])
    check("摄取结果已脱敏标记", ext1["deidentified"] is True)

    # 3. 化验单摄取（MOCK：文件名含"肝功"→ 化验样例，ALT 97 / GGT 64）
    lab_file = config.UPLOAD_DIR / "demo_肝功化验.jpg"
    lab_file.write_bytes(b"\xff\xd8\xff\xe0demo")
    r2 = ingest_document(pid, lab_file)
    alt = repo.get_timeline(pid, "ALT")
    ggt = repo.get_timeline(pid, "GGT")
    check("化验指标入库", len(alt) == 1 and alt[0]["value_num"] == 97
          and alt[0]["abnormal_flag"] == "H"
          and len(ggt) == 1 and ggt[0]["value_num"] == 64)

    # 4. 档案快照（阶段三 Agent 的输入）
    snap = repo.snapshot(pid)
    check("档案快照结构", set(snap) >= {"patient", "documents", "findings",
                                        "observations_timeline", "observations_latest"}
          and len(snap["documents"]) == 2
          and snap["observations_latest"]["ALT"]["value_num"] == 97,
          f"documents={len(snap['documents'])}, "
          f"obs={len(snap['observations_timeline'])}")

    # 5. 脱敏正则（含真实报告单版式的 PII 组合）
    dirty = ("姓名：何鑫 门诊号：0052456252 超声号:US260330032 "
             "抚松县人民医院 内科专家诊室3 申请医师：闫存宝 检查医生：汤洪艳 "
             "打印员：赵丹丹 电话13912345678")
    clean = deid.scrub_text(dirty)
    check("脱敏：姓名/单号/医生/机构/电话全部清除",
          not deid.contains_pii(clean)
          and "何鑫" not in clean and "0052456252" not in clean
          and "抚松" not in clean and "闫存宝" not in clean
          and "汤洪艳" not in clean and "13912345678" not in clean,
          clean[:60] + "…")
    check("脱敏：不误伤临床文本",
          deid.scrub_text("患者空腹扫查，肝实质回声细密") == "患者空腹扫查，肝实质回声细密")

    # 6. 规则引擎（PaddleOCR 兜底路径）否定词处理
    ocr_like = ("检查日期:2026-03-30\n年 龄: 25 岁 性 别:女\n"
                "肝脏大小、形态正常，肝实质回声细密，分布欠均匀，未见明确占位。\n"
                "胰腺形态、大小正常，实质回声略强，欠均匀。胰管未见明显扩张。\n"
                "超声提示：\n1.脂肪肝\n2.胰腺改变请结合临床\n检查医生：某某")
    rule_res = extract_with_rules(ocr_like)
    liver = next(f for f in rule_res.findings if f.organ == "肝脏")
    panc = next(f for f in rule_res.findings if f.organ == "胰腺")
    check("规则引擎：印象/日期/年龄", rule_res.exam_date == "2026-03-30"
          and rule_res.patient.age_years == 25
          and rule_res.impressions[:2] == ["脂肪肝", "胰腺改变请结合临床"])
    check("规则引擎：否定词不误报", "占位" not in liver.flags and "扩张" not in panc.flags
          and "欠均匀" in liver.flags and "略强" in panc.flags,
          f"肝flags={liver.flags} 胰flags={panc.flags}")

    # 7. schema 严格校验：坏数据应被拒绝并给出可回喂的错误
    try:
        from_dict({"document_type": "lab_report", "observations": [
            {"code": "", "value_num": "abc"}]})
        check("schema 严格校验", False)
    except ValueError as e:
        check("schema 严格校验（错误可回喂 LLM 自修正）", "observations" in str(e))

    print(f"\n全部 {PASSED} 项通过  数据库：{config.DB_PATH}")
    print("提示：配置 ANTHROPIC_API_KEY 后重跑上传接口即走真实视觉抽取。")


if __name__ == "__main__":
    main()
