"""阶段四离线测试：生物计算客户端与执行器（MOCK）、前端静态资源。

运行：python tests/test_stage4.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SOULHEALTH_MOCK"] = "1"
os.environ["SOULHEALTH_BIOCOMPUTE"] = "mock"

from app import config                                  # noqa: E402
from app.agent import orchestrator                      # noqa: E402
from app.archive import repository as repo              # noqa: E402
from app.biocompute import afdb_client, evo2_client, runner   # noqa: E402
from app.ingest.pipeline import ingest_document         # noqa: E402

PASSED = 0


def check(name, cond, detail=""):
    global PASSED
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)
    PASSED += 1


def main():
    # AlphaFold DB 客户端（MOCK 缓存）
    r = afdb_client.fetch_structure("PNPLA3", "Q9NST1")
    check("AFDB：PNPLA3/Q9NST1 结构与 pLDDT",
          r["status"] == "done" and r["source"] == "mock_cache"
          and r["mean_plddt"] > 0 and "alphafold.ebi.ac.uk" in r["page_url"],
          f"pLDDT={r['mean_plddt']}")
    r2 = afdb_client.fetch_structure("TM6SF2", None)
    check("AFDB：TM6SF2 无 UniProt → 待在线解析（诚实降级）",
          r2["status"] == "pending_resolution" and "在线解析" in r2["note"])

    # EVO2 客户端（MOCK 缓存）
    e = evo2_client.score_variant("PNPLA3", "rs738409 (I148M)")
    check("EVO2：rs738409 ΔlogL 与解读",
          e["status"] == "done" and e["delta_ll"] < 0
          and "演示" in e["interpretation"], f"ΔlogL={e['delta_ll']}")

    # 执行器回填：走一次完整分析后重放计划
    repo.init()
    pid = repo.create_patient(sex="female", age_years=25, height_cm=163, weight_kg=83)
    for fname in ("d4_ultrasound.jpg", "d4_肝功.jpg"):
        f = config.UPLOAD_DIR / fname
        f.write_bytes(b"\xff\xd8\xff\xe0x")
        ingest_document(pid, f)
    result = orchestrator.run_analysis(pid)
    check("编排器：EXEC_BIOCOMPUTE 节点在 trace 中",
          any(s["step"] == "EXEC_BIOCOMPUTE" for s in result["trace"]))
    redo = runner.execute_and_store(result["analysis_id"])
    stored = repo.get_analysis(result["analysis_id"])["biocompute"]
    check("执行器：结果回填 analyses 表",
          len(stored) == len(redo)
          and sum(1 for b in stored if b["status"] == "done") == 5
          and sum(1 for b in stored if b["status"] == "pending_resolution") == 1)

    # 健康报告包含生物计算结果与演示标注
    md = next(Path(r_["path"]) for r_ in result["reports"]
              if r_["report_type"] == "health_analysis" and r_["format"] == "md")
    text = md.read_text(encoding="utf-8")
    check("健康报告：pLDDT/ΔlogL/演示缓存标注在场",
          "pLDDT" in text and "logL" in text and "演示缓存" in text
          and "alphafold.ebi.ac.uk" in text)

    # 前端静态资源
    for f in ("index.html", "app.css", "app.js"):
        check(f"前端资源在位：static/{f}", (config.BASE_DIR / "static" / f).exists())
    html = (config.BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    check("前端：流程轴七节点与免责声明",
          html.count("data-step=") == 7 and "不替代医生诊断" in html)

    print(f"\n全部 {PASSED} 项通过 ✔")


if __name__ == "__main__":
    main()
