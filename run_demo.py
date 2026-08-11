"""一键端到端演示（命令行版）。

真实模式：配置 ANTHROPIC_API_KEY 后运行，抽取/问答走真实模型；
离线演示：未配置密钥时本脚本会自动显式开启 SOULHEALTH_MOCK=1 并提示。

用法：
  python run_demo.py            # 找回/建立演示患者档案并分析
  python run_demo.py --fresh    # 先清空数据库再演示
图形界面请运行 python run_api.py 后打开 http://127.0.0.1:8000/
"""
import os
import sys

# 必须在导入 app 之前决定模式（config 在导入时读取环境变量）
if not os.getenv("ANTHROPIC_API_KEY", "").strip() and \
   os.getenv("SOULHEALTH_MOCK", "").strip() != "1":
    os.environ["SOULHEALTH_MOCK"] = "1"
    print("[提示] 未配置 ANTHROPIC_API_KEY：本次演示自动显式开启 MOCK 模式"
          "（演示样例数据，报告会如实标注）。配置密钥后重跑即为真实抽取。\n")

from app import config                       # noqa: E402
from app.agent import orchestrator           # noqa: E402
from app.archive import repository as repo   # noqa: E402
from app.ingest.pipeline import ingest_document  # noqa: E402

if "--fresh" in sys.argv and config.DB_PATH.exists():
    config.DB_PATH.unlink()

repo.init()
print(f"运行环境：{config.runtime_info()}\n")

pid, created = repo.find_or_create_patient(
    name="演示患者", sex="female", age_years=25, height_cm=163, weight_kg=83,
    id_last4="0000")  # 固定后四位，脚本重复运行时精确找回同一条演示档案
p = repo.get_patient(pid)
print(f"① {'建档完成' if created else '找回既有档案'}：{p['name']}（{p['pseudonym']}）")

if config.MOCK_MODE:
    for fname, label in (("demo_超声报告.jpg", "腹部超声"), ("demo_肝功化验.jpg", "肝功化验")):
        f = config.UPLOAD_DIR / fname
        f.write_bytes(b"\xff\xd8\xff\xe0demo")
        r = ingest_document(pid, f)
        print(f"② 摄取{label}：engine={r['engine']}，已脱敏入档")
else:
    print("② 真实模式：请通过前端上传实际报告图片（本脚本不注入演示图）")

result = orchestrator.run_analysis(pid)
print("\n③ Agent 分析 trace：")
for s in result["trace"]:
    print(f"   [{s['step']:<17}] {s['title']}：{s['detail']}（{s['ms']} ms）")

print("\n④ 报告产物：")
for r in result["reports"]:
    print(f"   {r['title']}（{r['format']}）→ {r['path']}")
print("\n完成 ✔  前端演示：python run_api.py 后访问 http://127.0.0.1:8000/")
